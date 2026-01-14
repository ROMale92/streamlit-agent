import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import math
import io

st.set_page_config(layout="wide")
st.title("Persistenza terapeutica – Gap-based (solo date) + Kaplan–Meier + Log-rank (v1)")

# -------------------- Session state --------------------
for k in ["base_df", "meta", "full", "included"]:
    if k not in st.session_state:
        st.session_state[k] = None

# -------------------- Log-rank utilities (come il tuo) --------------------
def _gammainc_P(a: float, x: float, eps: float = 1e-12, max_iter: int = 10000) -> float:
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

def logrank_prism(times, events, groups, debug=False):
    df = pd.DataFrame({"time": times, "event": events, "group": groups})
    event_times = np.sort(df.loc[(df["event"] == 1) & (df["time"] > 0), "time"].unique())
    groups_unique = sorted(df["group"].unique())
    k = len(groups_unique)
    if k < 2 or event_times.size == 0:
        return math.nan, math.nan, k, pd.DataFrame()

    debug_rows = []

    if k == 2:
        O1, E1, V1 = 0.0, 0.0, 0.0
        g1, g2 = groups_unique
        for t in event_times:
            R = int((df["time"] >= t).sum())
            d = int(((df["time"] == t) & (df["event"] == 1)).sum())
            if R <= 1 or d == 0:
                continue
            R1 = int(((df["group"] == g1) & (df["time"] >= t)).sum())
            R2 = R - R1
            d1 = int(((df["group"] == g1) & (df["time"] == t) & (df["event"] == 1)).sum())
            E1_t = d * (R1 / R)
            V1_t = (R1 * R2 * d * (R - d)) / (R**2 * (R - 1))
            O1 += d1
            E1 += E1_t
            V1 += V1_t
            if debug:
                debug_rows.append({"time": t, "R": R, "d": d, "R1": R1, "R2": R2,
                                   "d1": d1, "E1_t": E1_t, "V1_t": V1_t})
        chi2_stat = (O1 - E1) ** 2 / V1 if V1 > 0 else math.nan
        pval = 1.0 - chi2_cdf(chi2_stat, 1)
        return chi2_stat, pval, k, pd.DataFrame(debug_rows)

    else:
        O = np.zeros(k)
        E = np.zeros(k)
        V = np.zeros((k, k))
        for t in event_times:
            R = int((df["time"] >= t).sum())
            d = int(((df["time"] == t) & (df["event"] == 1)).sum())
            if R <= 1 or d == 0:
                continue
            Rg = np.array([int(((df["group"] == g) & (df["time"] >= t)).sum()) for g in groups_unique])
            dg = np.array([int(((df["group"] == g) & (df["time"] == t) & (df["event"] == 1)).sum()) for g in groups_unique])
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
            return math.nan, math.nan, k, pd.DataFrame()
        dfree = k - 1
        pval = 1.0 - chi2_cdf(chi2_stat, dfree)
        return chi2_stat, pval, k, pd.DataFrame()

# -------------------- KM curve --------------------
def km_curve_from_times(times, events, period):
    df = pd.DataFrame({"time": times, "event": events}).sort_values("time")
    df = df[df["time"] > 0]  # ok: evita scalini strani a 0
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

# -------------------- Core: gap-based persistence with only dates --------------------
def compute_gap_persistence_for_patient(dates, start, end_obs, allowable_gap_days):
    """
    dates: array-like of timestamps sorted ascending (dispensations)
    start: first disp date (timestamp)
    end_obs: end of observation (min(start+period, cutoff_db))
    allowable_gap_days: int

    Event happens when next_date - current_date > allowable_gap_days.
    Event time is set at current_date + allowable_gap_days (end of grace), truncated to end_obs.
    """
    if len(dates) == 0:
        return 0, 0, "No dispensations"

    # ensure sorted unique
    dates = pd.to_datetime(pd.Series(dates)).dropna().sort_values().drop_duplicates().tolist()

    # single dispensation: cannot observe a gap -> censored at end_obs
    if len(dates) == 1:
        t = max((end_obs - start).days, 0)
        return int(min(t, (end_obs - start).days)), 0, "Censored (single disp)"

    # find first gap > allowable_gap
    for i in range(len(dates) - 1):
        gap = (dates[i+1] - dates[i]).days
        if gap > allowable_gap_days:
            event_date = dates[i] + pd.Timedelta(days=int(allowable_gap_days))
            event_date = min(event_date, end_obs)
            t = max((event_date - start).days, 0)
            return int(t), 1, f"Event (gap {gap} > {allowable_gap_days})"

    # no discontinuation observed -> censored at end_obs
    t = max((end_obs - start).days, 0)
    return int(t), 0, "Censored (no gap exceed)"

def preprocess_gap_based(df, id_col, date_col, group_col, period_days, cutoff_db, allowable_gap_days,
                        line_col=None, line_value=None, exclude_single=False):
    """
    Returns:
      full: per-patient summary with time/event + reasons + start/end_obs
      included: subset included (after optional exclusions)
    """
    d = df.copy()
    d[date_col] = pd.to_datetime(d[date_col], errors="coerce", dayfirst=True)
    d = d.dropna(subset=[date_col])

    # optional line filter
    if line_col and line_value is not None:
        d = d[d[line_col] == line_value].copy()

    rows = []
    for pid, g in d.groupby(id_col):
        g = g.sort_values(date_col)
        start = g[date_col].min()
        # end of observation: harmonize to period and cap at cutoff_db (database end)
        end_obs = min(start + pd.Timedelta(days=int(period_days)), cutoff_db)

        group_val = g[group_col].mode().iloc[0] if not g[group_col].mode().empty else "NA"
        dates = g[date_col].tolist()

        time, event, reason = compute_gap_persistence_for_patient(
            dates=dates,
            start=start,
            end_obs=end_obs,
            allowable_gap_days=int(allowable_gap_days),
        )

        n_disp = len(pd.Series(dates).dropna())
        incl = True
        if exclude_single and n_disp < 2:
            incl = False

        rows.append({
            "paziente": str(pid),
            "gruppo": str(group_val),
            "start": start,
            "end_obs": end_obs,
            "n_disp": int(n_disp),
            "time": int(time),
            "event": int(event),
            "incluso": bool(incl),
            "motivo": reason
        })

    full = pd.DataFrame(rows)
    included = full[full["incluso"]].copy()
    return full, included

# -------------------- UI --------------------
file_disp = st.file_uploader("📁 Carica file Excel con dispensazioni", type=["xlsx"])

if not file_disp:
    st.info("Carica un file Excel per iniziare.")
    st.stop()

df = pd.read_excel(file_disp)
st.success("✅ File caricato")
with st.expander("Anteprima dati", expanded=False):
    st.dataframe(df.head())

# ---- Setup form ----
with st.form("setup"):
    col1, col2, col3 = st.columns(3)
    with col1:
        id_col = st.selectbox("Colonna identificativo paziente", df.columns)
        date_col = st.selectbox("Colonna data dispensazione", df.columns)

    with col2:
        group_col = st.selectbox("Variabile di gruppo (ATC / categoria / principio attivo)", [c for c in df.columns if c != id_col])
        # filtro dinamico dei valori
        valori = sorted(df[group_col].dropna().astype(str).unique().tolist())
        gruppi_sel = st.multiselect(f"Filtra valori di '{group_col}' (opzionale)", options=valori, default=valori)

        # linea opzionale
        line_col = st.selectbox("Colonna Linea (opzionale)", options=["(nessuna)"] + list(df.columns), index=0)

    with col3:
        periodo = st.number_input("Orizzonte (giorni) – es. 365 per 1 anno", min_value=30, max_value=1825, value=365, step=30)
        gap = st.number_input("Allowable gap (giorni)", min_value=0, max_value=365, value=60, step=10)

        tmp_dates = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True)
        default_cutoff = tmp_dates.dropna().max()
        if pd.isna(default_cutoff):
            default_cutoff = pd.Timestamp.today()
        cutoff = st.date_input("Cutoff database (fine osservazione massima)", value=default_cutoff.date())

    col4, col5 = st.columns(2)
    with col4:
        exclude_single = st.checkbox("Escludi pazienti con 1 sola dispensazione", value=False)
    with col5:
        debug_opt = st.checkbox("Mostra tabella debug log-rank", value=False)

    submitted = st.form_submit_button("Avvia analisi")

# ---- Run ----
if submitted:
    d = df.copy()

    # filtro gruppi scelti
    if gruppi_sel and len(gruppi_sel) < len(valori):
        d = d[d[group_col].astype(str).isin(set(gruppi_sel))].copy()

    cutoff_ts = pd.to_datetime(cutoff)

    # linea target se presente
    line_value = None
    if line_col != "(nessuna)":
        line_values = sorted(d[line_col].dropna().unique().tolist())
        if len(line_values) > 0:
            line_value = st.selectbox("🎯 Seleziona Linea target", options=line_values)
        else:
            st.warning("La colonna Linea selezionata non ha valori validi; ignoro il filtro linea.")
            line_col = "(nessuna)"

    full, included = preprocess_gap_based(
        d, id_col, date_col, group_col,
        period_days=int(periodo),
        cutoff_db=cutoff_ts,
        allowable_gap_days=int(gap),
        line_col=None if line_col == "(nessuna)" else line_col,
        line_value=line_value,
        exclude_single=exclude_single,
    )

    st.subheader("📄 Tabella preprocessata (tutti i pazienti)")
    st.dataframe(full, use_container_width=True)

    st.subheader("✅ Pazienti inclusi (time/event)")
    st.dataframe(included[["paziente","gruppo","time","event","n_disp","motivo"]], use_container_width=True)

    # KM: anche con 1 gruppo
    if included.empty:
        st.info("Nessun paziente incluso: impossibile generare curve.")
    else:
        st.subheader("📈 Curve Kaplan–Meier (armonizzate all’orizzonte scelto)")
        fig = go.Figure()
        for strat, g in included.groupby("gruppo"):
            t_coords, s_coords = km_curve_from_times(g["time"].to_numpy(), g["event"].to_numpy(), int(periodo))
            fig.add_trace(go.Scatter(x=t_coords, y=s_coords, mode="lines+markers", line_shape="hv", name=str(strat)))
        fig.update_layout(
            xaxis_title="Giorni",
            yaxis_title="Probabilità di persistenza",
            yaxis=dict(range=[0, 1]),
        )
        st.plotly_chart(fig, use_container_width=True)

        # Log-rank: solo se ≥2 gruppi e almeno 1 evento totale
        if included["gruppo"].nunique() >= 2 and included["event"].sum() > 0:
            st.subheader("📊 Test log-rank (Mantel–Cox)")
            chi2_stat, pval, k, debug_df = logrank_prism(
                included["time"].to_numpy(),
                included["event"].to_numpy(),
                included["gruppo"].to_numpy(),
                debug=debug_opt
            )
            if math.isnan(chi2_stat):
                st.info("Test non calcolabile.")
            else:
                st.write(f"χ² = {chi2_stat:.3f} (df = {k-1}), p-value = {pval:.4g}")

            if debug_opt and not debug_df.empty:
                st.subheader("🔎 Debug log-rank")
                st.dataframe(debug_df, use_container_width=True)
        else:
            st.info("Log-rank non eseguito: serve ≥2 gruppi e almeno 1 evento.")

        # Export
        st.subheader("📥 Scarica risultati")
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            full.to_excel(writer, index=False, sheet_name="preprocess_all")
            included.to_excel(writer, index=False, sheet_name="time_event_included")
            if included["gruppo"].nunique() >= 2 and included["event"].sum() > 0:
                pd.DataFrame([{"chi2": chi2_stat, "df": k-1, "p_value": pval}]).to_excel(writer, index=False, sheet_name="logrank")
                if debug_opt and "debug_df" in locals() and not debug_df.empty:
                    debug_df.to_excel(writer, index=False, sheet_name="debug_logrank")

        st.download_button(
            "💾 Scarica Excel completo",
            data=buffer.getvalue(),
            file_name="persistenza_gap_based_solo_date.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
