"""
web/ui_components.py
====================
Componenti grafici, helper di stile e funzioni di presentazione condivise
per la Web UI di lotto-stat-app.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional, Sequence

import plotly.graph_objects as go
import streamlit as st

_CSS_PATH = Path(__file__).resolve().parent / "style.css"


def inject_custom_css() -> None:
    """Inietta il foglio di stile style.css nella pagina Streamlit."""
    if _CSS_PATH.exists():
        with open(_CSS_PATH, "r", encoding="utf-8") as f:
            css_content = f.read()
        st.markdown(f"<style>{css_content}</style>", unsafe_allow_html=True)


def render_header(
    title: str,
    subtitle: str,
    icon: str = "🎱",
    badges: Optional[Sequence[tuple[str, str]]] = None,
) -> None:
    """Renderizza la testata Hero con titolo, icona, sottotitolo ed eventuali badge di stato.
    badges: lista di tuple (testo, colore: 'green' | 'amber' | 'red' | 'blue')
    """
    inject_custom_css()
    badges_html = ""
    if badges:
        badge_spans = []
        for text, color in badges:
            badge_spans.append(
                f'<span class="status-chip status-chip-{color}">'
                f'<span class="status-dot status-dot-{color}"></span>{text}</span>'
            )
        badges_html = f"<div style='display: flex; gap: 8px; margin-top: 10px;'>{''.join(badge_spans)}</div>"

    html = f"""
    <div class="hero-header">
        <div class="hero-title">
            <span>{icon}</span> {title}
        </div>
        <p class="hero-subtitle">{subtitle}</p>
        {badges_html}
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def render_lotto_ball(number: int, variant: str = "lotto", size: str = "md") -> str:
    """Ritorna l'HTML per una singola pallina del Lotto o SuperEnalotto.
    variant: 'lotto' (oro), 'sen' (smeraldo), 'jolly' (rosso), 'superstar' (viola), 'neutral' (grigio)
    size: 'sm', 'md', 'lg'
    """
    size_class = f"lotto-ball-{size}" if size in ("sm", "lg") else ""
    variant_class = f"lotto-ball-{variant}" if variant != "lotto" else ""
    return f'<div class="lotto-ball {size_class} {variant_class}">{number:02d}</div>'


def render_balls_row(
    numbers: Iterable[int],
    variant: str = "lotto",
    size: str = "md",
    title: Optional[str] = None,
) -> None:
    """Renderizza una riga di palline numerate in un container flex."""
    balls_html = "".join(render_lotto_ball(n, variant=variant, size=size) for n in sorted(numbers))
    title_html = f"<div style='font-size: 0.85rem; font-weight: 600; color: #94a3b8; margin-bottom: 4px;'>{title}</div>" if title else ""
    st.markdown(
        f"{title_html}<div class='lotto-balls-container'>{balls_html}</div>",
        unsafe_allow_html=True,
    )


def apply_plotly_theme(fig: go.Figure, height: int = 380) -> go.Figure:
    """Applica un tema scuro moderno e coerente a qualsiasi grafico Plotly."""
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(15, 23, 42, 0)",
        plot_bgcolor="rgba(30, 41, 59, 0.35)",
        font=dict(family="sans-serif", color="#F8FAFC", size=12),
        height=height,
        margin=dict(l=40, r=20, t=50, b=40),
        xaxis=dict(
            gridcolor="rgba(255, 255, 255, 0.07)",
            zerolinecolor="rgba(255, 255, 255, 0.12)",
        ),
        yaxis=dict(
            gridcolor="rgba(255, 255, 255, 0.07)",
            zerolinecolor="rgba(255, 255, 255, 0.12)",
        ),
        legend=dict(
            bgcolor="rgba(15, 23, 42, 0.7)",
            bordercolor="rgba(255, 255, 255, 0.1)",
            borderwidth=1,
        ),
        hoverlabel=dict(
            bgcolor="#1E293B",
            font_size=12,
            font_family="sans-serif",
        ),
    )
    return fig


def render_quick_nav() -> None:
    """Renderizza le card di navigazione rapida verso i 4 moduli parametrici."""
    st.markdown("<h4 style='color: #F8FAFC; margin: 18px 0 10px 0;'>🔍 Moduli di Analisi Specialistica</h4>", unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        with st.container(border=True):
            st.page_link("pages/1_📈_Analizzatore_Somme.py", label="**Analizzatore Somme**", icon="📈")
            st.caption("Distribuzione reale vs normale teorica con Z-score e curtosi.")
    with c2:
        with st.container(border=True):
            st.page_link("pages/2_🎲_Simulatore_Backtest.py", label="**Simulatore Backtest**", icon="🎲")
            st.caption("Testing retroattivo combinazioni con calcolo Yield e Max Drawdown.")
    with c3:
        with st.container(border=True):
            st.page_link("pages/3_🔢_Tabellone_Analitico.py", label="**Tabellone Analitico**", icon="🔢")
            st.caption("Matrice 1-90 con Heatmap IRR e isocronia inter-ruota.")
    with c4:
        with st.container(border=True):
            st.page_link("pages/4_🕵️_Numero_Spia.py", label="**Numero Spia**", icon="🕵️")
            st.caption("Frequenza condizionata post-spia con Indice di Attrattiva.")

