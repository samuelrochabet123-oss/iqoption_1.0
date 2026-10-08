# ================================================================
# IQ OPTION BOT V1.4.2 (TESTE RÁPIDO DE ENTRADAS + DASHBOARD)
# ================================================================

import os
import time
import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import gspread

from google.oauth2.service_account import Credentials
from iqoptionapi.stable_api import IQ_Option


# ================================================================
# ESTADO GLOBAL DO BOT (COMPARTILHADO COM O HTTP)
# ================================================================

estado_bot = {
    "par": os.getenv("PAR", "EURUSD").upper(),
    "status_stream": "Aguardando...",
    "vela_atual": None,
    "ultimo_sinal": None,
    "suporte": None,
    "resistencia": None,
    "pavio_atual": 0.0,
    "resumo_placar": {"wins": 0, "losses": 0, "assertividade": 0.0}
}


# ================================================================
# DASHBOARD HTTP & API ENDPOINTS
# ================================================================

HTML_DASHBOARD = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>IQ Option Bot - Live Dashboard & Auto Trader</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        body { background-color: #0f172a; color: #f8fafc; display: flex; flex-direction: column; align-items: center; min-height: 100vh; padding: 20px; }
        .container { width: 100%; max-width: 800px; display: grid; gap: 20px; }
        .card { background: #1e293b; border-radius: 12px; padding: 20px; box-shadow: 0 4px 15px rgba(0,0,0,0.3); border: 1px solid #334155; }
        .header { display: flex; justify-content: space-between; align-items: center; }
        .badge { background: #22c55e; padding: 4px 12px; border-radius: 20px; font-weight: bold; font-size: 0.9em; color: #0f172a; }
        
        /* TIMING / RELÓGIO */
        .timer-box { text-align: center; padding: 30px; background: #090d16; border-radius: 12px; border: 2px solid #334155; }
        .timer-title { font-size: 1em; color: #94a3b8; text-transform: uppercase; letter-spacing: 1px; }
        .timer-clock { font-size: 4em; font-weight: 800; font-family: monospace; color: #38bdf8; margin: 10px 0; }
        .timer-warning { color: #ef4444 !important; animation: pulse 0.8s infinite alternate; }

        /* SINAL DE ENTRADA */
        .signal-box { text-align: center; padding: 25px; border-radius: 12px; font-weight: bold; font-size: 1.8em; transition: all 0.3s ease; }
        .signal-NEUTRO { background: #1e293b; color: #64748b; border: 1px dashed #475569; }
        .signal-CALL { background: #15803d; color: #ffffff; border: 2px solid #22c55e; animation: flash 1s infinite alternate; }
        .signal-PUT { background: #b91c1c; color: #ffffff; border: 2px solid #ef4444; animation: flash 1s infinite alternate; }

        /* VELAS & METRICAS */
        .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
        .metric { display: flex; flex-direction: column; gap: 5px; }
        .metric-label { color: #94a3b8; font-size: 0.85em; }
        .metric-value { font-size: 1.2em; font-weight: 600; font-family: monospace; }

        @keyframes pulse { from { transform: scale(1); } to { transform: scale(1.05); } }
        @keyframes flash { from { opacity: 1; } to { opacity: 0.8; } }
    </style>
</head>
<body>
    <div class="container">
        <div class="card header">
            <h2>IQ OPTION AUTO-BOT <span id="par-ativo" style="color: #38bdf8;">--</span></h2>
            <span class="badge" id="status-bot">AUTO TRADER ATIVO</span>
        </div>

        <div class="card timer-box">
            <div class="timer-title">Tempo Restante da Vela M1</div>
            <div class="timer-clock" id="clock">00s</div>
            <p id="timer-hint" style="color: #64748b; font-size: 0.9em;">Execução automática habilitada na Conta Treino</p>
        </div>

        <div class="card">
            <div class="metric-label" style="margin-bottom: 10px;">ÚLTIMA ORDEM EXECUTADA:</div>
            <div id="signal-card" class="signal-box signal-NEUTRO">AGUARDANDO OPORTUNIDADE</div>
            <div style="margin-top: 15px; font-size: 0.9em; text-align: center; color: #94a3b8;" id="signal-details">
                Rejeição Pavio: -- | Entrada Prevista: --
            </div>
        </div>

        <div class="card grid-2">
            <div class="metric">
                <span class="metric-label">Preço Atual:</span>
                <span class="metric-value" id="preco-atual">0.00000</span>
            </div>
            <div class="metric">
                <span class="metric-label">Rejeição Pavio (Atual):</span>
                <span class="metric-value" id="pavio-atual" style="color: #f59e0b;">0%</span>
            </div>
            <div class="metric">
                <span class="metric-label">Resistência (Topo):</span>
                <span class="metric-value" id="resistencia" style="color: #ef4444;">0.00000</span>
            </div>
            <div class="metric">
                <span class="metric-label">Suporte (Fundo):</span>
                <span class="metric-value" id="suporte" style="color: #22c55e;">0.00000</span>
            </div>
        </div>
    </div>

    <audio id="audio-alert" src="https://actions.google.com/sounds/v1/alarms/beep_short.ogg" preload="auto"></audio>

    <script>
        let ultimoSinalNotificado = "";

        function atualizarDashboard() {
            fetch('/api/status')
                .then(res => res.json())
                .then(data => {
                    document.getElementById('par-ativo').innerText = data.par;
                    document.getElementById('status-bot').innerText = "AUTO DEMO: " + data.status_stream;
                    
                    if (data.vela_atual) {
                        document.getElementById('preco-atual').innerText = data.vela_atual.close.toFixed(5);
                    }
                    
                    document.getElementById('pavio-atual').innerText = data.pavio_atual + '%';
                    document.getElementById('resistencia').innerText = data.resistencia ? data.resistencia.toFixed(5) : '--';
                    document.getElementById('suporte').innerText = data.suporte ? data.suporte.toFixed(5) : '--';

                    const signalCard = document.getElementById('signal-card');
                    if (data.ultimo_sinal && data.ultimo_sinal.sinal !== "NEUTRO") {
                        const s = data.ultimo_sinal;
                        signalCard.className = `signal-box signal-${s.sinal}`;
                        signalCard.innerText = `ORDEM ENVIADA: ${s.sinal}`;
                        document.getElementById('signal-details').innerText = 
                            `Pavio: ${s.pavio_ratio}% | Executada em: ${s.datetime_entrada}`;

                        if (ultimoSinalNotificado !== s.datetime_sinal) {
                            ultimoSinalNotificado = s.datetime_sinal;
                            document.getElementById('audio-alert').play().catch(()=>{});
                        }
                    } else {
                        signalCard.className = "signal-box signal-NEUTRO";
                        signalCard.innerText = "AGUARDANDO OPORTUNIDADE";
                        document.getElementById('signal-details').innerText = "Aguardando confirmação de exaustão e rejeição";
                    }
                })
                .catch(err => console.error(err));
        }

        function atualizarRelogio() {
            const agora = new Date();
            const segundos = agora.getSeconds();
            const restantes = 60 - segundos;
            const clockEl = document.getElementById('clock');
            const hintEl = document.getElementById('timer-hint');

            clockEl.innerText = (restantes < 10 ? "0" : "") + restantes + "s";

            if (restantes <= 5) {
                clockEl.classList.add('timer-warning');
                hintEl.innerText = "ENTRADA AUTOMÁTICA PRESTES A SER DISPARADA!";
                hintEl.style.color = "#ef4444";
                hintEl.style.fontWeight = "bold";
            } else {
                clockEl.classList.remove('timer-warning');
                hintEl.innerText = "Execução automática habilitada na Conta Treino";
                hintEl.style.color = "#64748b";
                hintEl.style.fontWeight = "normal";
            }
        }

        setInterval(atualizarDashboard, 1000);
        setInterval(atualizarRelogio, 1000);
        atualizarRelogio();
    </script>
</body>
</html>
"""


class DashboardHTTPHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/status":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(estado_bot).encode("utf-8"))
        else:
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_DASHBOARD.encode("utf-8"))

    def log_message(self, format, *args):
        return


def iniciar_servidor_http():
    porta = int(os.getenv("PORT", 8080))
    servidor = HTTPServer(("0.0.0.0", porta), DashboardHTTPHandler)
    log.info(f"Dashboard HTTP rodando na porta {porta} (Render / Web).")
    servidor.serve_forever()


# ================================================================
# CONFIGURAÇÕES
# ================================================================

PAR = os.getenv("PAR", "EURUSD").upper()

TIMEFRAME = 60  # M1
SR_PERIODO = 20

# PARÂMETROS PARA TESTE RÁPIDO DE DISPAROS:
EXAUSTAO_FATOR_TAMANHO = 1.2  # Exige vela apenas 20% maior que a média
MIN_PAVIO_RATIO = 0.20        # Pavio de rejeição mínimo reduzido para 20%

COOLDOWN_VELAS = 2
ultimo_sinal_timestamp = 0

STREAM_MAXDICT = 20

MODO_AUTO = True
VALOR_ENTRADA = float(os.getenv("VALOR_ENTRADA", 10.0))

GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "1uuw_jS5-e4dUQ28DCffknMaMnJfUbekHLkTFp0aqGtA")

ABA_COLETAS = "IQOption_Coletas"
ABA_SINAIS = "Sinais_Bot"
ABA_RESUMO = "Resumo"

IQ_EMAIL = os.getenv("IQ_EMAIL")
IQ_PASSWORD = os.getenv("IQ_PASSWORD")

GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TZ_LOCAL = ZoneInfo("America/Sao_Paulo")

LOOP_SECONDS = 1
HISTORICO_CANDLES = 100

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("IQOPTION-BOT-V1.4.2")

HEADER_SINAIS = ["datetime_sinal", "par", "estrategia", "pavio_ratio", "sinal", "datetime_entrada", "entrada", "datetime_resultado", "saida", "resultado", "saldo_wl", "status"]
HEADER_RESUMO = ["estrategia", "total_sinais", "wins", "losses", "empates", "assertividade", "saldo_wl", "maior_loss", "atual_loss"]


def para_float(valor):
    if valor is None or valor == "":
        return 0.0
    if isinstance(valor, (int, float)):
        return float(valor)
    return float(str(valor).replace(",", ".").strip())


def timestamp_para_local(timestamp):
    return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).astimezone(TZ_LOCAL)


def formatar_datetime(timestamp):
    return timestamp_para_local(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def normalizar_candle(timestamp, candle):
    return {
        "timestamp": int(timestamp),
        "datetime": formatar_datetime(timestamp),
        "open": float(candle["open"]),
        "high": float(candle["max"]),
        "low": float(candle["min"]),
        "close": float(candle["close"]),
        "volume": float(candle.get("volume", 0))
    }


def validar_configuracao():
    obrigatorias = {"IQ_EMAIL": IQ_EMAIL, "IQ_PASSWORD": IQ_PASSWORD, "GOOGLE_CREDENTIALS_JSON": GOOGLE_CREDENTIALS_JSON, "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN, "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID}
    faltando = [nome for nome, valor in obrigatorias.items() if not valor]
    if faltando:
        raise RuntimeError("Variáveis de ambiente ausentes: " + ", ".join(faltando))


def conectar_google():
    info = json.loads(GOOGLE_CREDENTIALS_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    credentials = Credentials.from_service_account_info(info, scopes=scopes)
    client = gspread.authorize(credentials)
    return client.open_by_key(GOOGLE_SHEET_ID)


def obter_aba(spreadsheet, nome):
    try:
        return spreadsheet.worksheet(nome)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=nome, rows=5000, cols=20)


def garantir_cabecalho(aba, cabecalho):
    try:
        if not aba.row_values(1):
            aba.update(range_name="A1", values=[cabecalho])
    except Exception:
        pass


def telegram_enviar(mensagem):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": mensagem}
    try:
        requests.post(url, json=payload, timeout=15)
        return True
    except Exception:
        return False


def carregar_timestamps_coletas(aba):
    try:
        valores = aba.col_values(1)
    except Exception as e:
        log.warning(f"Erro lendo coluna de candles: {e}")
        return set()

    timestamps = set()
    for valor in valores:
        if not valor or valor.lower() == "datetime":
            continue
        try:
            dt = datetime.strptime(valor.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ_LOCAL)
            timestamps.add(int(dt.timestamp()))
        except Exception:
            continue

    return timestamps


def registrar_candle(aba, candle):
    linha = [
        candle["datetime"],
        PAR,
        candle["open"],
        candle["high"],
        candle["low"],
        candle["close"] if candle["close"] is not None else "",
        candle["volume"]
    ]

    try:
        aba.append_row(linha, value_input_option="USER_ENTERED")
        return True
    except Exception as e:
        log.warning(f"Erro registrando candle na planilha: {e}")
        return False


def calcular_suporte_resistencia(historico, periodo=20):
    if len(historico) < periodo:
        return None, None
    velas_recentes = historico[-periodo:]
    return min(c["low"] for c in velas_recentes), max(c["high"] for c in velas_recentes)


def gerar_sinal_estrategia(historico, timestamp_atual):
    global ultimo_sinal_timestamp

    if len(historico) < SR_PERIODO + 10:
        return "NEUTRO", None

    suporte, resistencia = calcular_suporte_resistencia(historico[:-1], SR_PERIODO)
    
    estado_bot["suporte"] = suporte
    estado_bot["resistencia"] = resistencia

    if (timestamp_atual - ultimo_sinal_timestamp) < (COOLDOWN_VELAS * TIMEFRAME):
        return "NEUTRO", None

    if suporte is None or resistencia is None:
        return "NEUTRO", None

    vela_atual = historico[-1]
    velas_anteriores = historico[-11:-1]

    abertura, fechamento = vela_atual["open"], vela_atual["close"]
    maxima, minima = vela_atual["high"], vela_atual["low"]

    tamanho_total = maxima - minima
    if tamanho_total == 0:
        return "NEUTRO", None

    tamanhos_anteriores = [c["high"] - c["low"] for c in velas_anteriores]
    media_tamanho = sum(tamanhos_anteriores) / len(tamanhos_anteriores) if tamanhos_anteriores else 0.0001

    eh_exaustao = tamanho_total >= (media_tamanho * EXAUSTAO_FATOR_TAMANHO)

    pavio_superior = maxima - max(abertura, fechamento)
    pavio_inferior = min(abertura, fechamento) - minima

    ratio_pavio_sup = pavio_superior / tamanho_total
    ratio_pavio_inf = pavio_inferior / tamanho_total

    pavio_perc = round(max(ratio_pavio_sup, ratio_pavio_inf) * 100, 2)
    estado_bot["pavio_atual"] = pavio_perc

    if eh_exaustao and maxima >= resistencia and fechamento > abertura and ratio_pavio_sup >= MIN_PAVIO_RATIO:
        ultimo_sinal_timestamp = timestamp_atual
        return "PUT", round(ratio_pavio_sup * 100, 2)

    if eh_exaustao and minima <= suporte and fechamento < abertura and ratio_pavio_inf >= MIN_PAVIO_RATIO:
        ultimo_sinal_timestamp = timestamp_atual
        return "CALL", round(ratio_pavio_inf * 100, 2)

    return "NEUTRO", pavio_perc


def conectar_iq():
    api = IQ_Option(IQ_EMAIL, IQ_PASSWORD)
    try:
        api.set_max_reconnect(-1)
    except Exception:
        pass
    conectado, motivo = api.connect()
    if not conectado:
        raise RuntimeError(f"Falha na conexão IQ Option: {motivo}")
    api.change_balance("PRACTICE")
    log.info("IQ Option conectada | CONTA: PRACTICE (DEMONSTRAÇÃO)")
    return api


def reinstanciar_e_reconectar_iq():
    while True:
        try:
            api = conectar_iq()
            iniciar_stream(api)
            return api
        except Exception:
            time.sleep(15)


def iniciar_stream(api):
    api.start_candles_stream(PAR, TIMEFRAME, STREAM_MAXDICT)
    time.sleep(2)


def obter_stream_candles(api):
    dados = api.get_realtime_candles(PAR, TIMEFRAME)
    if not dados:
        return []
    candles = []
    for timestamp, candle in dados.items():
        try:
            if "open" in candle:
                candles.append(normalizar_candle(timestamp, candle))
        except Exception:
            pass
    candles.sort(key=lambda x: x["timestamp"])
    return candles


def obter_historico_inicial(api):
    try:
        dados = api.get_candles(PAR, TIMEFRAME, HISTORICO_CANDLES, int(time.time()))
        candles = [normalizar_candle(c["from"], c) for c in dados if "from" in c]
        candles.sort(key=lambda x: x["timestamp"])
        return candles
    except Exception:
        return []


def executar_ordem_iq(api, sinal):
    if not MODO_AUTO:
        return False, None

    direcao = "buy" if sinal == "CALL" else "sell"
    log.info(f"⚡ ENVIANDO ORDEM PARA A IQ OPTION | Direção: {sinal} | Valor: {VALOR_ENTRADA}")

    try:
        status, id_ordem = api.buy(VALOR_ENTRADA, PAR, direcao, 1)
        if status:
            log.info(f"✅ ORDEM EXECUTADA COM SUCESSO! | ID: {id_ordem}")
            return True, id_ordem
        else:
            log.error(f"❌ REJEIÇÃO DA ORDEM PELA CORRETORA: {id_ordem}")
            return False, str(id_ordem)
    except Exception as e:
        log.exception(f"Erro ao disparar ordem via API: {e}")
        return False, str(e)


def carregar_sinais(aba):
    try:
        valores = aba.get_all_values()
        sinais = {}
        for numero_linha, row in enumerate(valores[1:], start=2):
            if len(row) >= 12:
                sinais[row[0]] = {"linha": numero_linha, "datetime_sinal": row[0], "sinal": row[4], "datetime_entrada": row[5], "entrada": row[6], "status": row[11], "pavio_ratio": row[3]}
        return sinais
    except Exception:
        return {}


def criar_sinal_simulado(aba, api, candle_fechado, candle_entrada, pavio_ratio, sinal, sinais_existentes):
    datetime_sinal = candle_fechado["datetime"]
    if datetime_sinal in sinais_existentes:
        return False

    sucesso_ordem, id_ordem = executar_ordem_iq(api, sinal)

    linha = [
        candle_fechado["datetime"],
        PAR,
        "Price Action + Exaustao",
        f"{pavio_ratio}%",
        sinal,
        candle_entrada["datetime"],
        candle_entrada["open"],
        "",
        "",
        "AGUARDANDO",
        f"ID: {id_ordem}" if id_ordem else "",
        "ABERTO"
    ]

    try:
        aba.append_row(linha, value_input_option="USER_ENTERED")
        
        estado_bot["ultimo_sinal"] = {
            "sinal": sinal,
            "pavio_ratio": pavio_ratio,
            "datetime_sinal": candle_fechado["datetime"],
            "datetime_entrada": candle_entrada["datetime"]
        }

        telegram_enviar(
            "🚀 ORDEM ENVIADA — CONTA TREINO\n\n"
            f"Par: {PAR} | Operação: {sinal}\n"
            f"Valor: R$/$ {VALOR_ENTRADA}\n"
            f"ID da Ordem: {id_ordem if id_ordem else 'Falha na execução'}\n"
            f"Entrada em: {candle_entrada['datetime']} (Preço: {candle_entrada['open']})"
        )
        return True
    except Exception:
        return False


def determinar_resultado(sinal, entrada, saida):
    entrada = para_float(entrada)
    saida = para_float(saida)

    if saida == entrada:
        return "EMPATE"
    if sinal == "CALL":
        return "WIN" if saida > entrada else "LOSS"
    if sinal == "PUT":
        return "WIN" if saida < entrada else "LOSS"

    return "EMPATE"


def atualizar_resultados(aba_sinais, sinais, candle_fechado):
    if not sinais:
        return 0

    alterados = 0
    ts_fechado = candle_fechado["timestamp"]

    for dt_sinal, sinal in list(sinais.items()):
        if sinal.get("status") != "ABERTO":
            continue

        try:
            dt_entrada = datetime.strptime(
                sinal["datetime_entrada"],
                "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=TZ_LOCAL)

            ts_entrada = int(dt_entrada.timestamp())

            if ts_fechado >= ts_entrada:
                entrada = para_float(sinal["entrada"])
                saida = para_float(candle_fechado["close"])

                resultado = determinar_resultado(sinal["sinal"], entrada, saida)
                linha = sinal["linha"]

                if not linha:
                    continue

                aba_sinais.update(
                    range_name=f"H{linha}:L{linha}",
                    values=[[
                        candle_fechado["datetime"],
                        saida,
                        resultado,
                        "",
                        "FECHADO"
                    ]],
                    value_input_option="USER_ENTERED"
                )

                sinal["datetime_resultado"] = candle_fechado["datetime"]
                sinal["saida"] = saida
                sinal["resultado"] = resultado
                sinal["status"] = "FECHADO"

                alterados += 1
                emoji = {"WIN": "✅", "LOSS": "❌", "EMPATE": "⚪"}[resultado]

                log.info("=" * 50)
                log.info(f"{emoji} RESULTADO CONFIRMADO | {sinal['sinal']} | Entrada: {entrada} | Saída: {saida} | Resultado: {resultado}")
                log.info("=" * 50)

                telegram_enviar(
                    f"{emoji} RESULTADO — CONTA TREINO\n\n"
                    f"Par: {PAR} | Operação: {sinal['sinal']}\n"
                    f"Entrada: {entrada} | Saída: {saida}\n"
                    f"Resultado: {resultado}"
                )

        except Exception as e:
            log.exception(f"Erro fechando sinal {dt_sinal}: {e}")

    return alterados


def calcular_resumo(sinais):
    wins = 0
    losses = 0
    empates = 0
    atual_loss = 0
    maior_loss = 0

    ordenados = sorted(sinais.values(), key=lambda x: x["datetime_sinal"])

    for sinal in ordenados:
        resultado = str(sinal.get("resultado", "")).upper()
        if resultado == "WIN":
            wins += 1
            atual_loss = 0
        elif resultado == "LOSS":
            losses += 1
            atual_loss += 1
            maior_loss = max(maior_loss, atual_loss)
        elif resultado == "EMPATE":
            empates += 1
            atual_loss = 0

    total = wins + losses + empates
    assertividade = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0.0
    saldo = wins - losses

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "empates": empates,
        "assertividade": assertividade,
        "saldo": saldo,
        "maior_loss": maior_loss,
        "atual_loss": atual_loss
    }


def atualizar_resumo(aba_resumo, sinais):
    resumo = calcular_resumo(sinais)
    linha = [
        "Price Action + Exaustao",
        resumo["total"],
        resumo["wins"],
        resumo["losses"],
        resumo["empates"],
        round(resumo["assertividade"], 2),
        resumo["saldo"],
        resumo["maior_loss"],
        resumo["atual_loss"]
    ]

    try:
        aba_resumo.update(
            range_name="A1:I2",
            values=[HEADER_RESUMO, linha],
            value_input_option="USER_ENTERED"
        )
    except Exception as e:
        log.warning(f"Erro atualizando resumo: {e}")

    return resumo


def main():
    threading.Thread(target=iniciar_servidor_http, daemon=True).start()
    validar_configuracao()

    spreadsheet = conectar_google()
    aba_coletas = obter_aba(spreadsheet, ABA_COLETAS)
    aba_sinais = obter_aba(spreadsheet, ABA_SINAIS)
    aba_resumo = obter_aba(spreadsheet, ABA_RESUMO)

    garantir_cabecalho(aba_sinais, HEADER_SINAIS)
    garantir_cabecalho(aba_resumo, HEADER_RESUMO)

    timestamps_coletas = carregar_timestamps_coletas(aba_coletas)
    sinais = carregar_sinais(aba_sinais)

    log.info(f"Candles já gravados na planilha: {len(timestamps_coletas)}")
    log.info(f"Sinais já salvos na planilha: {len(sinais)}")

    api = None
    while True:
        try:
            api = conectar_iq()
            break
        except Exception:
            time.sleep(15)

    historico = obter_historico_inicial(api)
    iniciar_stream(api)

    ultimo_timestamp_processado = None

    while True:
        try:
            if not api.check_connect():
                api = reinstanciar_e_reconectar_iq()
                continue

            candles_stream = obter_stream_candles(api)
            if not candles_stream:
                time.sleep(LOOP_SECONDS)
                continue

            vela_em_andamento = candles_stream[-1]
            estado_bot["vela_atual"] = vela_em_andamento
            estado_bot["status_stream"] = "ONLINE (AUTO-TRADER)"

            timestamp_atual = vela_em_andamento["timestamp"]

            if ultimo_timestamp_processado is None:
                ultimo_timestamp_processado = timestamp_atual
                time.sleep(LOOP_SECONDS)
                continue

            # ====================================================
            # VIRADA DE VELA (M1 ENCERROU)
            # ====================================================
            if timestamp_atual > ultimo_timestamp_processado:
                mapa = {c["timestamp"]: c for c in candles_stream}
                ts_fechada = timestamp_atual - TIMEFRAME

                vela_fechada = mapa.get(ts_fechada)
                vela_entrada = mapa.get(timestamp_atual)

                if vela_fechada:
                    # 1. SALVA A VELA NA ABA 'IQOption_Coletas'
                    if ts_fechada not in timestamps_coletas:
                        sucesso = registrar_candle(aba_coletas, vela_fechada)
                        if sucesso:
                            timestamps_coletas.add(ts_fechada)
                            log.info(f"📊 CANDLE REGISTRADO | {vela_fechada['datetime']} | O={vela_fechada['open']} | C={vela_fechada['close']}")

                    # 2. ATUALIZA RESULTADOS DE SINAIS ABERTOS NA PLANILHA
                    sinais = carregar_sinais(aba_sinais)
                    resultados = atualizar_resultados(aba_sinais, sinais, vela_fechada)
                    if resultados > 0:
                        sinais = carregar_sinais(aba_sinais)
                        atualizar_resumo(aba_resumo, sinais)

                    # 3. ATUALIZA HISTÓRICO DE ANÁLISE
                    historico = [c for c in historico if c["timestamp"] != vela_fechada["timestamp"]]
                    historico.append(vela_fechada)
                    historico.sort(key=lambda x: x["timestamp"])
                    historico = historico[-HISTORICO_CANDLES:]

                    # 4. GERA SINAL E DISPARA OPERAÇÃO AUTOMÁTICA
                    sinal, pavio_ratio = gerar_sinal_estrategia(historico, timestamp_atual)

                    if sinal in ("CALL", "PUT") and vela_entrada:
                        criar_sinal_simulado(aba_sinais, api, vela_fechada, vela_entrada, pavio_ratio, sinal, sinais)
                        sinais = carregar_sinais(aba_sinais)
                        atualizar_resumo(aba_resumo, sinais)

                ultimo_timestamp_processado = timestamp_atual

            time.sleep(LOOP_SECONDS)

        except Exception as e:
            log.exception(f"Erro loop principal: {e}")
            time.sleep(10)


if __name__ == "__main__":
    main()
