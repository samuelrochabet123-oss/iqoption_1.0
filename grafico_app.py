import time
import os
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from iqoptionapi.stable_api import IQ_Option

# Configuração da página
st.set_page_config(page_title="Gráfico ao Vivo - M1", layout="wide")
st.title("📈 Gráfico de Velas em Tempo Real (M1)")

# Conexão IQ Option apenas para leitura do gráfico
IQ_EMAIL = os.getenv("IQ_EMAIL")
IQ_PASSWORD = os.getenv("IQ_PASSWORD")
PAR = os.getenv("PAR", "EURUSD").upper()

@st.cache_resource
def iniciar_conexao():
    api = IQ_Option(IQ_EMAIL, IQ_PASSWORD)
    api.connect()
    api.change_balance("PRACTICE")
    return api

api = iniciar_conexao()

# Container para o gráfico
placeholder = st.empty()

# Loop de atualização do gráfico
while True:
    # Busca os últimos 40 candles de M1
    candles = api.get_candles(PAR, 60, 40, time.time())
    
    if candles:
        df = pd.DataFrame(candles)
        df['datetime'] = pd.to_datetime(df['from'], unit='s')

        # Criando o gráfico Candlestick profissional
        fig = go.Figure(data=[go.Candlestick(
            x=df['datetime'],
            open=df['open'],
            high=df['max'],
            low=df['min'],
            close=df['close'],
            increasing_line_color='#00c853', # Verde
            decreasing_line_color='#ff3d00'  # Vermelho
        )])

        fig.update_layout(
            title=f"Par: {PAR} | Timeframe: M1",
            xaxis_rangeslider_visible=False,
            template="plotly_dark",
            height=600,
            margin=dict(l=20, r=20, t=40, b=20)
        )

        # Atualiza a tela a cada 1 segundo
        with placeholder.container():
            st.plotly_chart(fig, use_container_width=True)

    time.sleep(1)
