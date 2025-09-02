

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import math
import io

st.set_page_config(layout="wide")
st.title("Persistenza terapeutica – Kaplan–Meier (Plotly) + Log-rank multi-gruppo (senza SciPy)") 

# -------------------- Incomplete gamma & Chi-square CDF (no SciPy) --------------------
def _gammainc_P(a: float, x: float, eps: float = 1e-12, max_iter: int = 10000) -> float:
    """
    Regularized lower incomplete gamma P(a, x) using:
    - series expansion when x < a + 1
    - continued fraction (Lentz) via Q(a, x) when x >= a + 1
    Based on Numerical Recipes 3rd ed.
    """
    if x <= 0:
        return 0.0
    # Series expansion for P(a,x)
    if x < a + 1.0:
        term = 1.0 / a
        summ = term
        n = 1
        while n < max_iter:
            term *= x / (a + n)
            summ += term
            if abs(term) < abs(summ) * eps:
                break
            n += 1
        return summ * math.exp(-x + a * math.log(x) - math.lgamma(a))
    # Continued fraction for Q(a,x); then P = 1 - Q
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b if b != 0 else 1.0 / tiny
    h = d
    for i in range(1, max_iter + 1):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    Q = math.exp(-x + a * math.log(x) - math.lgamma(a)) * h
    P = 1.0 - Q
    return P

def chi2_cdf(x: float, df: int) -> float:
    """CDF of Chi-square with df degrees using regularized gamma."""
    if x < 0 or df <= 0:
        return 0.0
    a = 0.5 * df
    return _gammainc_P(a, 0.5 * x)

# -------------------- Kaplan–Meier & Log-rank (multi-gruppo) --------------------
def build_patient_times(df, id_col, date_col, strat_col, period, cutoff):
    """
    Crea dataset per KM normalizzato:
    - includi pazienti che hanno interrotto ENTRO cutoff, oppure
      che hanno follow-up >= period alla data cutoff.
    - time = min(giorni osservati, period)
    - event = 1 se interruzione prima di period, 0 se censura a period.
    """
    rows = []
    for pid, g in df.groupby(id_col):
        start = g[date_col].min()
        last = g[date_col].max()
        # eleggibilità
        eligible = (last <= cutoff) or ((cutoff - start).days >= period)
        if not eligible:
            continue

        observed_last = min(last, cutoff)
        days = (observed_last - start).days
        time = int(min(days, period))
        # Evento se ultimo entro cutoff ed ha meno di period
        event = 1 if (last <= cutoff and days < period) else 0
        strat = g[strat_col].mode().iloc[0] if not g[strat_col].mode().empty else "NA"
        rows.append({"paziente": pid, "time": time, "event": int(event), strat_col: strat,
                     "start": start.date(), "last": last.date(), "eligible": eligible})
    return pd.DataFrame(rows)

def km_curve_from_times(times, events, period):
    """Compute KM step curve coordinates up to 'period'."""
    df = pd.DataFrame({"time": times, "event": events}).sort_values("time")
    df = df[df["time"] >= 0]
    S = 1.0
    t_coords = [0]
    s_coords = [1.0]
    at_risk = len(df)
    for t in df["time"].unique():
        if t > period:
            break
        d = int(df[(df["time"] == t) & (df["event"] == 1)].shape[0])
        c = int(df[(df["time"] == t) & (df["event"] == 0)].shape[0])
        if d > 0 and at_risk > 0:
            S *= (at_risk - d) / at_risk
        at_risk -= (d + c)
        t_coords.append(int(t))
        s_coords.append(S)
    if t_coords[-1] < period:
        t_coords.append(int(period))
        s_coords.append(S)
    return t_coords, s_coords

def logrank_multigroup(times, events, groups):
    """
    Multi-sample log-rank test senza SciPy.
    Restituisce (chi2_stat, p_value, k).
    """
    df = pd.DataFrame({"time": times, "event": events, "group": groups})
    event_times = np.sort(df.loc[df["event"] == 1, "time"].unique())
    k = df["group"].nunique()
    if k < 2 or event_times.size == 0:
        return math.nan, math.nan, k

    group_order = sorted(df["group"].unique())
    k = len(group_order)

    O = np.zeros(k)
    E = np.zeros(k)
    V = np.zeros((k, k))

    for t in event_times:
        # a rischio prima di t
        at_risk_mask = df["time"] >= t
        R = int(at_risk_mask.sum())
        if R <= 1:
            continue
        # eventi a t
        d = int(((df["time"] == t) & (df["event"] == 1)).sum())
        if d == 0 or d == R:
            continue

        Rg = df.groupby("group")["time"].apply(lambda s: int((s >= t).sum())).reindex(group_order).fillna(0).to_numpy(dtype=float)
        dg = df.groupby("group").apply(lambda g: int(((g["time"] == t) & (g["event"] == 1)).sum())).reindex(group_order).fillna(0).to_numpy(dtype=float)

        Eg = d * (Rg / R)

        common = d * (R - d) / (R**2 * (R - 1))
        V += np.diag(Rg * (R - Rg) * common)
        V -= np.outer(Rg, Rg) * common

        O += dg
        E += Eg

    D = O - E
    try:
        Vinv = np.linalg.pinv(V)
        chi2_stat = float(D.T @ Vinv @ D)
    except Exception:
        return math.nan, math.nan, k

    dfree = k - 1
    pval = 1.0 - chi2_cdf(chi2_stat, dfree)
    return chi2_stat, pval, k

# -------------------- UI --------------------
file_disp = st.file_uploader("📁 Carica file Excel con dispensazioni", type=["xlsx"])

if file_disp:
    df = pd.read_excel(file_disp)
    st.success("✅ File caricato")

    with st.expander("Anteprima dati", expanded=False):
        st.dataframe(df.head())

    with st.form("setup"):
        col1, col2 = st.columns(2)
        with col1:
            id_col = st.selectbox("Colonna identificativo paziente", df.columns)
            date_col = st.selectbox("Colonna data dispensazione", df.columns)
            strat_col = st.selectbox("Variabile di stratificazione (ATC / principio / categoria)", [c for c in df.columns if c != id_col])
        with col2:
            periodo = st.number_input("Periodo di osservazione (giorni)", min_value=30, max_value=1825, value=365, step=30)
            cutoff = st.date_input("Data indice (cutoff)", value=pd.to_datetime(df[date_col]).max())
        submitted = st.form_submit_button("Avvia analisi")

    if submitted:
        # Prep
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True)
        df = df.dropna(subset=[date_col])

        # Build times dataset normalizzato
        km_df = build_patient_times(df, id_col, date_col, strat_col, int(periodo), pd.to_datetime(cutoff))

        if km_df.empty:
            st.warning("Nessun paziente eleggibile con le regole di normalizzazione.")
        else:
            # Plot KM per gruppo
            fig = go.Figure()
            for strat, g in km_df.groupby(strat_col):
                t_coords, s_coords = km_curve_from_times(g["time"].to_numpy(), g["event"].to_numpy(), int(periodo))
                fig.add_trace(go.Scatter(x=t_coords, y=s_coords, mode="lines+markers",
                                         line_shape="hv", name=str(strat)))
            fig.update_layout(xaxis_title="Giorni", yaxis_title="Probabilità di persistenza", yaxis=dict(range=[0,1]))
            st.subheader("📈 Curva Kaplan–Meier di persistenza (normalizzata)")
            st.plotly_chart(fig, use_container_width=True)

            # Riepilogo punti fissi
            st.subheader("📊 Riepilogo punti fissi")
            summary_rows = []
            for strat, g in km_df.groupby(strat_col):
                t_coords, s_coords = km_curve_from_times(g["time"].to_numpy(), g["event"].to_numpy(), int(periodo))
                for pt in [180, 365, 730]:
                    if pt <= int(periodo):
                        idx = max(i for i, tt in enumerate(t_coords) if tt <= pt)
                        summary_rows.append({"Strato": strat, "Giorni": pt, "Persistenti": round(s_coords[idx]*100, 2)})
            summary_df = pd.DataFrame(summary_rows)
            st.dataframe(summary_df)

            # Log-rank multi-gruppo (senza SciPy)
            chi2_stat, pval, k = logrank_multigroup(km_df["time"].to_numpy(), km_df["event"].to_numpy(), km_df[strat_col].to_numpy())
            st.subheader("📊 Test log-rank (multi-gruppo)")
            if math.isnan(chi2_stat):
                st.info("Test non calcolabile (eventi insufficienti o un solo gruppo).")
            else:
                st.write(f"χ² = {chi2_stat:.3f} (df = {k-1}), p-value = {pval:.4g}")

            # Download Excel
            st.subheader("📥 Scarica risultati")
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                km_df.to_excel(writer, index=False, sheet_name="km_data")
                summary_df.to_excel(writer, index=False, sheet_name="summary_points")
                pd.DataFrame([{"chi2": chi2_stat, "df": k-1, "p_value": pval}]).to_excel(writer, index=False, sheet_name="logrank")
            st.download_button(
                label="💾 Scarica Excel (KM + riepilogo + log-rank)",
                data=buffer.getvalue(),
                file_name="persistenza_km_results.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
else:
    st.info("Carica un file Excel per iniziare.")

