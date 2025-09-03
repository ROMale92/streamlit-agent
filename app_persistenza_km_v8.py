
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import math
import io

st.set_page_config(layout="wide")
st.title("Persistenza terapeutica – Kaplan–Meier stile Prism (Mantel–Cox log-rank)")

# -------------------- Funzioni matematiche --------------------
def _gammainc_P(a: float, x: float, eps: float = 1e-12, max_iter: int = 10000) -> float:
    """Regularized lower incomplete gamma P(a, x)."""
    if x <= 0:
        return 0.0
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
    return 1.0 - Q

def chi2_cdf(x: float, df: int) -> float:
    if x < 0 or df <= 0:
        return 0.0
    return _gammainc_P(0.5 * df, 0.5 * x)

# -------------------- Preprocessing stile Prism --------------------
def preprocess_prism(df, id_col, date_col, strat_col, period, cutoff_date):
    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True)
    invalid_dates = df[date_col].isna().sum()
    df = df.dropna(subset=[date_col])

    rows = []
    for pid, g in df.groupby(id_col):
        start = g[date_col].min()
        last  = g[date_col].max()
        observed_last = min(last, cutoff_date)
        observed_days = (observed_last - start).days

        include = False
        event = None
        reason = ""

        if last <= cutoff_date and observed_days < period:
            include = True
            event = 1
            reason = "Evento entro cutoff"
        elif observed_days >= period:
            include = True
            event = 0
            reason = "Censura (persistente >= periodo)"
        else:
            include = False
            event = 0
            reason = "Escluso (follow-up insufficiente e nessun evento)"

        time = int(min(observed_days, period))
        strat = g[strat_col].mode().iloc[0] if not g[strat_col].mode().empty else "NA"

        rows.append({
            "paziente": pid,
            "gruppo": strat,
            "start": start.date(),
            "last": last.date(),
            "cutoff_usato": cutoff_date.date(),
            "giorni_osservati": observed_days,
            "time": time,
            "event": int(event),
            "incluso": include,
            "motivo": reason
        })

    full = pd.DataFrame(rows)
    included = full[full["incluso"]].copy()
    return full, included, int(invalid_dates)

# -------------------- Kaplan–Meier --------------------
def km_curve_from_times(times, events, period):
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

# -------------------- Log-rank Mantel–Cox --------------------
def logrank_prism(times, events, groups, debug=False):
    df = pd.DataFrame({"time": times, "event": events, "group": groups})
    event_times = np.sort(df.loc[df["event"] == 1, "time"].unique())
    groups_unique = sorted(df["group"].unique())
    k = len(groups_unique)
    if k < 2 or event_times.size == 0:
        return math.nan, math.nan, k, pd.DataFrame()

    O = np.zeros(k)
    E = np.zeros(k)
    V = np.zeros((k, k))
    debug_rows = []

    for t in event_times:
        at_risk_mask = df["time"] >= t
        R = int(at_risk_mask.sum())
        if R <= 1:
            continue
        d = int(((df["time"] == t) & (df["event"] == 1)).sum())
        if d == 0:
            continue

        Rg = np.array([int(((df["group"] == g) & (df["time"] >= t)).sum()) for g in groups_unique])
        dg = np.array([int(((df["group"] == g) & (df["time"] == t) & (df["event"] == 1)).sum()) for g in groups_unique])
        Eg = d * (Rg / R)

        common = d * (R - d) / (R**2 * (R - 1))
        V += np.diag(Rg * (R - Rg) * common)
        V -= np.outer(Rg, Rg) * common

        O += dg
        E += Eg

        if debug:
            debug_rows.append({
                "time": t,
                "R": R,
                "d": d,
                **{f"Rg_{g}": Rg[i] for i, g in enumerate(groups_unique)},
                **{f"dg_{g}": dg[i] for i, g in enumerate(groups_unique)},
                **{f"Eg_{g}": Eg[i] for i, g in enumerate(groups_unique)}
            })

    D = O - E
    try:
        Vinv = np.linalg.pinv(V)
        chi2_stat = float(D.T @ Vinv @ D)
    except Exception:
        return math.nan, math.nan, k, pd.DataFrame()

    dfree = k - 1
    pval = 1.0 - chi2_cdf(chi2_stat, dfree)
    debug_df = pd.DataFrame(debug_rows)
    return chi2_stat, pval, k, debug_df

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
            tmp_dates = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True)
            default_cutoff = tmp_dates.dropna().max()
            if pd.isna(default_cutoff):
                default_cutoff = pd.Timestamp.today()
            cutoff = st.date_input("Data indice (cutoff)", value=default_cutoff.date())
        debug_opt = st.checkbox("Mostra tabella debug log-rank")
        submitted = st.form_submit_button("Avvia analisi")

    if submitted:
        cutoff_ts = pd.to_datetime(cutoff)
        full, included, invalid_n = preprocess_prism(df, id_col, date_col, strat_col, int(periodo), cutoff_ts)

        st.subheader("📄 Tabella preprocessata (tutti i pazienti)")
        st.dataframe(full)

        st.subheader("✅ Pazienti inclusi (tempo/evento)")
        st.dataframe(included[["paziente","gruppo","time","event"]])

        if included["gruppo"].nunique() < 2 or included.empty:
            st.info("Servono almeno 2 gruppi e almeno 1 evento per generare curve e test.")
        else:
            st.subheader("📈 Curve Kaplan–Meier")
            fig = go.Figure()
            for strat, g in included.groupby("gruppo"):
                t_coords, s_coords = km_curve_from_times(g["time"].to_numpy(), g["event"].to_numpy(), int(periodo))
                fig.add_trace(go.Scatter(x=t_coords, y=s_coords, mode="lines+markers",
                                         line_shape="hv", name=str(strat)))
            fig.update_layout(xaxis_title="Giorni", yaxis_title="Probabilità di persistenza", yaxis=dict(range=[0,1]))
            st.plotly_chart(fig, use_container_width=True)

            st.subheader("📊 Test log-rank (Mantel–Cox)")
            chi2_stat, pval, k, debug_df = logrank_prism(included["time"].to_numpy(), included["event"].to_numpy(), included["gruppo"].to_numpy(), debug=debug_opt)
            if math.isnan(chi2_stat):
                st.info("Test non calcolabile.")
            else:
                st.write(f"χ² = {chi2_stat:.3f} (df = {k-1}), p-value = {pval:.4g}")

            if debug_opt and not debug_df.empty:
                st.subheader("🔎 Tabella debug log-rank (per tempo evento)")
                st.dataframe(debug_df)

            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                full.to_excel(writer, index=False, sheet_name="preprocess_all")
                included.to_excel(writer, index=False, sheet_name="tempo_evento_inclusi")
                pd.DataFrame([{"chi2": chi2_stat, "df": k-1, "p_value": pval}]).to_excel(writer, index=False, sheet_name="logrank")
                if not debug_df.empty:
                    debug_df.to_excel(writer, index=False, sheet_name="debug_logrank")
            st.download_button("💾 Scarica Excel completo", data=buffer.getvalue(), file_name="persistenza_prism_v8.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
else:
    st.info("Carica un file Excel per iniziare.")
