# ================================================================
# IQ OPTION BOT V1.1
# ================================================================
#
# MODO: SIMULADOR
# ATIVO: definido pela variável PAR
# TIMEFRAME: M1
# ESTRATÉGIA: RSI 14 - 30/70
#
# FLUXO:
#
# VELA N FECHADA
#       ↓
# RSI 14
#       ↓
# CALL / PUT
#       ↓
# ABERTURA DA VELA N+1
#       ↓
# ENTRADA SIMULADA
#       ↓
# FECHAMENTO DA VELA N+1
#       ↓
# WIN / LOSS / EMPATE
#       ↓
# GOOGLE SHEETS + TELEGRAM
#
# ATENÇÃO:
# ESTE BOT NÃO EXECUTA ORDENS REAIS.
# ================================================================

import os
import time
import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import gspread

from google.oauth2.service_account import Credentials
from iqoptionapi.stable_api import IQ_Option


# ================================================================
# CONFIGURAÇÕES
# ================================================================

PAR = os.getenv("PAR", "EURUSD").upper()

TIMEFRAME = 60

RSI_PERIODO = 14
RSI_CALL = 30
RSI_PUT = 70

STREAM_MAXDICT = 20

GOOGLE_SHEET_ID = os.getenv(
    "GOOGLE_SHEET_ID",
    "1uuw_jS5-e4dUQ28DCffknMaMnJfUbekHLkTFp0aqGtA"
)

ABA_COLETAS = "IQOption_Coletas"
ABA_SINAIS = "Sinais_Bot"
ABA_RESUMO = "Resumo"

IQ_EMAIL = os.getenv("IQ_EMAIL")
IQ_PASSWORD = os.getenv("IQ_PASSWORD")

GOOGLE_CREDENTIALS_JSON = os.getenv(
    "GOOGLE_CREDENTIALS_JSON"
)

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN"
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID"
)

TZ_LOCAL = ZoneInfo("America/Sao_Paulo")

LOOP_SECONDS = 1

HISTORICO_CANDLES = 100


# ================================================================
# LOG
# ================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("IQOPTION-BOT-V1")


# ================================================================
# CABEÇALHOS
# ================================================================

HEADER_SINAIS = [
    "datetime_sinal",
    "par",
    "estrategia",
    "rsi",
    "sinal",
    "datetime_entrada",
    "entrada",
    "datetime_resultado",
    "saida",
    "resultado",
    "saldo_wl",
    "status"
]

HEADER_RESUMO = [
    "estrategia",
    "total_sinais",
    "wins",
    "losses",
    "empates",
    "assertividade",
    "saldo_wl",
    "maior_loss",
    "atual_loss"
]


# ================================================================
# UTILITÁRIOS
# ================================================================

def agora_local():
    return datetime.now(TZ_LOCAL)


def timestamp_para_local(timestamp):
    return datetime.fromtimestamp(
        int(timestamp),
        tz=timezone.utc
    ).astimezone(TZ_LOCAL)


def formatar_datetime(timestamp):
    return timestamp_para_local(timestamp).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


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


# ================================================================
# VALIDAÇÃO
# ================================================================

def validar_configuracao():

    obrigatorias = {
        "IQ_EMAIL": IQ_EMAIL,
        "IQ_PASSWORD": IQ_PASSWORD,
        "GOOGLE_CREDENTIALS_JSON": GOOGLE_CREDENTIALS_JSON,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID
    }

    faltando = [
        nome
        for nome, valor in obrigatorias.items()
        if not valor
    ]

    if faltando:
        raise RuntimeError(
            "Variáveis de ambiente ausentes: "
            + ", ".join(faltando)
        )

    if not GOOGLE_SHEET_ID:
        raise RuntimeError(
            "GOOGLE_SHEET_ID não configurado."
        )


# ================================================================
# GOOGLE SHEETS
# ================================================================

def conectar_google():

    log.info("Conectando ao Google Sheets...")

    try:
        info = json.loads(
            GOOGLE_CREDENTIALS_JSON
        )

    except Exception as e:

        raise RuntimeError(
            "GOOGLE_CREDENTIALS_JSON não contém "
            f"um JSON válido: {e}"
        )

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]

    credentials = (
        Credentials
        .from_service_account_info(
            info,
            scopes=scopes
        )
    )

    client = gspread.authorize(
        credentials
    )

    spreadsheet = client.open_by_key(
        GOOGLE_SHEET_ID
    )

    log.info(
        f"Planilha conectada: {spreadsheet.title}"
    )

    return spreadsheet


def obter_aba(spreadsheet, nome):

    try:

        return spreadsheet.worksheet(nome)

    except gspread.WorksheetNotFound:

        log.info(
            f"Criando aba '{nome}'..."
        )

        return spreadsheet.add_worksheet(
            title=nome,
            rows=5000,
            cols=20
        )


def garantir_cabecalho(aba, cabecalho):

    try:

        primeira_linha = aba.row_values(1)

        if not primeira_linha:

            aba.update(
                [cabecalho],
                "A1"
            )

    except Exception as e:

        log.warning(
            f"Não foi possível verificar cabeçalho "
            f"da aba {aba.title}: {e}"
        )


# ================================================================
# TELEGRAM
# ================================================================

def telegram_enviar(mensagem):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensagem
    }

    try:

        resposta = requests.post(
            url,
            json=payload,
            timeout=15
        )

        if resposta.status_code != 200:

            log.warning(
                "Telegram retornou erro: "
                f"{resposta.text}"
            )

            return False

        return True

    except Exception as e:

        log.warning(
            f"Erro enviando Telegram: {e}"
        )

        return False


# ================================================================
# RSI
# ================================================================

def calcular_rsi(closes, periodo=14):

    if len(closes) < periodo + 1:
        return None

    serie = pd.Series(
        closes,
        dtype="float64"
    )

    delta = serie.diff()

    ganhos = delta.clip(
        lower=0
    )

    perdas = -delta.clip(
        upper=0
    )

    media_ganho = ganhos.ewm(
        alpha=1 / periodo,
        adjust=False
    ).mean()

    media_perda = perdas.ewm(
        alpha=1 / periodo,
        adjust=False
    ).mean()

    ultimo_ganho = media_ganho.iloc[-1]
    ultima_perda = media_perda.iloc[-1]

    if ultima_perda == 0:

        if ultimo_ganho > 0:
            return 100.0

        return 50.0

    rs = ultimo_ganho / ultima_perda

    rsi = 100 - (
        100 / (1 + rs)
    )

    return float(rsi)


# ================================================================
# ESTRATÉGIA
# ================================================================

def gerar_sinal(rsi):

    if rsi is None:
        return "NEUTRO"

    if rsi <= RSI_CALL:
        return "CALL"

    if rsi >= RSI_PUT:
        return "PUT"

    return "NEUTRO"


# ================================================================
# IQ OPTION
# ================================================================

def conectar_iq():

    log.info(
        f"Conectando IQ Option | PAR={PAR}"
    )

    api = IQ_Option(
        IQ_EMAIL,
        IQ_PASSWORD
    )

    try:
        api.set_max_reconnect(-1)
    except Exception:
        pass

    conectado, motivo = api.connect()

    if not conectado:

        raise RuntimeError(
            f"Falha na conexão IQ Option: {motivo}"
        )

    # ============================================================
    # SEGURANÇA
    # ============================================================

    api.change_balance(
        "PRACTICE"
    )

    log.info(
        "IQ Option conectada."
    )

    log.info(
        "Conta selecionada: PRACTICE"
    )

    return api


def iniciar_stream(api):

    log.info(
        f"Iniciando stream {PAR} M1..."
    )

    api.start_candles_stream(
        PAR,
        TIMEFRAME,
        STREAM_MAXDICT
    )

    time.sleep(2)

    candles = api.get_realtime_candles(
        PAR,
        TIMEFRAME
    )

    if not candles:

        raise RuntimeError(
            "Stream iniciou, mas não retornou candles."
        )

    log.info(
        f"Stream ativo | {len(candles)} candles disponíveis."
    )


# ================================================================
# STREAM
# ================================================================

def obter_stream_candles(api):

    dados = api.get_realtime_candles(
        PAR,
        TIMEFRAME
    )

    if not dados:
        return []

    candles = []

    for timestamp, candle in dados.items():

        try:

            timestamp = int(timestamp)

            if "open" not in candle:
                continue

            candles.append(
                normalizar_candle(
                    timestamp,
                    candle
                )
            )

        except Exception as e:

            log.debug(
                f"Candle inválido ignorado: {e}"
            )

    candles.sort(
        key=lambda x: x["timestamp"]
    )

    return candles


# ================================================================
# HISTÓRICO
# ================================================================

def obter_historico_inicial(api):

    log.info(
        "Obtendo histórico inicial para calcular RSI..."
    )

    try:

        dados = api.get_candles(
            PAR,
            TIMEFRAME,
            HISTORICO_CANDLES,
            int(time.time())
        )

    except Exception as e:

        log.warning(
            f"Erro obtendo histórico: {e}"
        )

        return []

    if not dados:
        return []

    candles = []

    for candle in dados:

        try:

            timestamp = int(
                candle["from"]
            )

            candles.append(
                normalizar_candle(
                    timestamp,
                    candle
                )
            )

        except Exception:
            continue

    candles.sort(
        key=lambda x: x["timestamp"]
    )

    log.info(
        f"Histórico inicial: {len(candles)} candles."
    )

    return candles


# ================================================================
# GOOGLE - CANDLES
# ================================================================

def carregar_timestamps_coletas(aba):

    try:

        valores = aba.col_values(1)

    except Exception as e:

        log.warning(
            f"Erro lendo coluna de candles: {e}"
        )

        return set()

    timestamps = set()

    for valor in valores:

        if not valor:
            continue

        texto = valor.strip()

        if texto.lower() == "datetime":
            continue

        try:

            dt = datetime.strptime(
                texto,
                "%Y-%m-%d %H:%M:%S"
            )

            dt = dt.replace(
                tzinfo=TZ_LOCAL
            )

            timestamps.add(
                int(dt.timestamp())
            )

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
        candle["close"]
        if candle["close"] is not None
        else "",
        candle["volume"]
    ]

    try:

        aba.append_row(
            linha,
            value_input_option="USER_ENTERED"
        )

        return True

    except Exception as e:

        log.warning(
            f"Erro registrando candle: {e}"
        )

        return False


# ================================================================
# SINAIS
# ================================================================

def carregar_sinais(aba):

    try:

        valores = aba.get_all_values()

    except Exception as e:

        log.warning(
            f"Erro lendo Sinais_Bot: {e}"
        )

        return {}

    sinais = {}

    if len(valores) <= 1:
        return sinais

    for numero_linha, row in enumerate(
        valores[1:],
        start=2
    ):

        if len(row) < 12:
            continue

        try:

            dt_sinal = row[0]

            sinais[dt_sinal] = {
                "linha": numero_linha,
                "datetime_sinal": dt_sinal,
                "par": row[1],
                "estrategia": row[2],
                "rsi": row[3],
                "sinal": row[4],
                "datetime_entrada": row[5],
                "entrada": row[6],
                "datetime_resultado": row[7],
                "saida": row[8],
                "resultado": row[9],
                "saldo_wl": row[10],
                "status": row[11]
            }

        except Exception:
            continue

    return sinais


# ================================================================
# CRIAR SINAL
# ================================================================

def criar_sinal_simulado(
    aba,
    candle_fechado,
    candle_entrada,
    rsi,
    sinal,
    sinais_existentes
):

    datetime_sinal = candle_fechado["datetime"]

    if datetime_sinal in sinais_existentes:
        return False

    linha = [
        candle_fechado["datetime"],
        PAR,
        "RSI 30/70",
        round(rsi, 2),
        sinal,
        candle_entrada["datetime"],
        candle_entrada["open"],
        "",
        "",
        "AGUARDANDO",
        "",
        "ABERTO"
    ]

    try:

        aba.append_row(
            linha,
            value_input_option="USER_ENTERED"
        )

        log.info(
            "=================================================="
        )

        log.info(
            f"🚨 NOVO SINAL | {sinal}"
        )

        log.info(
            f"RSI: {rsi:.2f}"
        )

        log.info(
            f"Vela do sinal: "
            f"{candle_fechado['datetime']}"
        )

        log.info(
            f"Entrada: "
            f"{candle_entrada['datetime']}"
        )

        log.info(
            f"Preço entrada: "
            f"{candle_entrada['open']}"
        )

        log.info(
            "=================================================="
        )

        telegram_enviar(
            "🚨 NOVO SINAL — SIMULADOR\n\n"
            f"Par: {PAR}\n"
            f"Estratégia: RSI 30/70\n"
            f"RSI: {rsi:.2f}\n"
            f"Sinal: {sinal}\n\n"
            f"Vela do sinal:\n"
            f"{candle_fechado['datetime']}\n\n"
            f"ENTRADA:\n"
            f"{candle_entrada['datetime']}\n"
            f"Preço: {candle_entrada['open']}\n\n"
            "⏳ Resultado será calculado "
            "no fechamento da vela de entrada."
        )

        return True

    except Exception as e:

        log.warning(
            f"Erro criando sinal: {e}"
        )

        return False


# ================================================================
# RESULTADO
# ================================================================

def determinar_resultado(
    sinal,
    entrada,
    saida
):

    entrada = float(entrada)
    saida = float(saida)

    if saida == entrada:
        return "EMPATE"

    if sinal == "CALL":

        if saida > entrada:
            return "WIN"

        return "LOSS"

    if sinal == "PUT":

        if saida < entrada:
            return "WIN"

        return "LOSS"

    return "EMPATE"


# ================================================================
# ATUALIZA RESULTADOS
# ================================================================

def atualizar_resultados(
    aba_sinais,
    sinais,
    candle_fechado
):

    if not sinais:
        return 0

    alterados = 0

    ts_fechado = candle_fechado["timestamp"]

    # ------------------------------------------------------------
    # O candle_fechado é justamente o candle que acabou de
    # terminar.
    #
    # Procuramos sinais cuja vela de entrada seja exatamente
    # esse candle.
    # ------------------------------------------------------------

    for dt_sinal, sinal in list(
        sinais.items()
    ):

        if sinal["status"] != "ABERTO":
            continue

        try:

            dt_entrada = datetime.strptime(
                sinal["datetime_entrada"],
                "%Y-%m-%d %H:%M:%S"
            )

            dt_entrada = dt_entrada.replace(
                tzinfo=TZ_LOCAL
            )

            ts_entrada = int(
                dt_entrada.timestamp()
            )

            # ----------------------------------------------------
            # Só fecha este sinal se a vela que acabou de fechar
            # for exatamente a vela da entrada.
            # ----------------------------------------------------

            if ts_entrada != ts_fechado:
                continue

            entrada = float(
                sinal["entrada"]
            )

            saida = float(
                candle_fechado["close"]
            )

            resultado = determinar_resultado(
                sinal["sinal"],
                entrada,
                saida
            )

            linha = sinal["linha"]

            if not linha:
                continue

            # ----------------------------------------------------
            # Atualiza Google Sheets
            # ----------------------------------------------------

            aba_sinais.update(
                f"H{linha}:L{linha}",
                [[
                    candle_fechado["datetime"],
                    saida,
                    resultado,
                    "",
                    "FECHADO"
                ]],
                value_input_option="USER_ENTERED"
            )

            # ----------------------------------------------------
            # Atualiza memória
            # ----------------------------------------------------

            sinal["datetime_resultado"] = (
                candle_fechado["datetime"]
            )

            sinal["saida"] = saida

            sinal["resultado"] = resultado

            sinal["status"] = "FECHADO"

            alterados += 1

            emoji = {
                "WIN": "✅",
                "LOSS": "❌",
                "EMPATE": "⚪"
            }[resultado]

            log.info(
                "=================================================="
            )

            log.info(
                f"{emoji} RESULTADO CONFIRMADO"
            )

            log.info(
                f"Sinal: {sinal['sinal']}"
            )

            log.info(
                f"Entrada: {entrada}"
            )

            log.info(
                f"Saída: {saida}"
            )

            log.info(
                f"Resultado: {resultado}"
            )

            log.info(
                "=================================================="
            )

            # ----------------------------------------------------
            # TELEGRAM
            # ----------------------------------------------------

            telegram_enviar(
                f"{emoji} RESULTADO — SIMULADOR\n\n"
                f"Par: {PAR}\n"
                f"Estratégia: RSI 30/70\n"
                f"RSI: {sinal['rsi']}\n"
                f"Sinal: {sinal['sinal']}\n\n"
                f"Entrada: {entrada}\n"
                f"Saída: {saida}\n"
                f"Resultado: {resultado}\n\n"
                f"Fechamento: "
                f"{candle_fechado['datetime']}"
            )

        except Exception as e:

            log.exception(
                f"Erro fechando sinal "
                f"{dt_sinal}: {e}"
            )

    return alterados


# ================================================================
# RESUMO
# ================================================================

def calcular_resumo(sinais):

    wins = 0
    losses = 0
    empates = 0

    atual_loss = 0
    maior_loss = 0

    ordenados = sorted(
        sinais.values(),
        key=lambda x: x["datetime_sinal"]
    )

    for sinal in ordenados:

        resultado = str(
            sinal.get("resultado", "")
        ).upper()

        if resultado == "WIN":

            wins += 1
            atual_loss = 0

        elif resultado == "LOSS":

            losses += 1
            atual_loss += 1

            maior_loss = max(
                maior_loss,
                atual_loss
            )

        elif resultado == "EMPATE":

            empates += 1
            atual_loss = 0

    total = (
        wins +
        losses +
        empates
    )

    if wins + losses > 0:

        assertividade = (
            wins /
            (wins + losses)
        ) * 100

    else:

        assertividade = 0.0

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


def atualizar_resumo(
    aba_resumo,
    sinais
):

    resumo = calcular_resumo(
        sinais
    )

    linha = [
        "RSI 30/70",
        resumo["total"],
        resumo["wins"],
        resumo["losses"],
        resumo["empates"],
        round(
            resumo["assertividade"],
            2
        ),
        resumo["saldo"],
        resumo["maior_loss"],
        resumo["atual_loss"]
    ]

    try:

        aba_resumo.update(
            "A1:I2",
            [
                HEADER_RESUMO,
                linha
            ],
            value_input_option="USER_ENTERED"
        )

    except Exception as e:

        log.warning(
            f"Erro atualizando resumo: {e}"
        )

    return resumo


# ================================================================
# TELEGRAM STATUS
# ================================================================

def telegram_online():

    telegram_enviar(
        "🤖 IQ OPTION BOT V1.1\n\n"
        "🟢 ONLINE\n\n"
        f"Par: {PAR}\n"
        "Timeframe: M1\n"
        "Estratégia: RSI 30/70\n"
        "RSI: 14\n"
        "Modo: SIMULADOR\n\n"
        "⚠️ Nenhuma ordem real será executada."
    )


def telegram_reconectado():

    telegram_enviar(
        "🔄 IQ OPTION BOT V1.1\n\n"
        "Conexão restabelecida.\n\n"
        f"Par: {PAR}\n"
        "Modo: SIMULADOR"
    )


# ================================================================
# PROCESSAMENTO DA VELA FECHADA
# ================================================================

def processar_vela_fechada(
    vela_fechada,
    vela_entrada,
    historico,
    aba_coletas,
    aba_sinais,
    timestamps_coletas,
    sinais
):

    ts = vela_fechada["timestamp"]

    # ------------------------------------------------------------
    # 1. SALVA CANDLE
    # ------------------------------------------------------------

    if ts not in timestamps_coletas:

        sucesso = registrar_candle(
            aba_coletas,
            vela_fechada
        )

        if sucesso:

            timestamps_coletas.add(
                ts
            )

            log.info(
                f"📊 CANDLE | "
                f"{vela_fechada['datetime']} | "
                f"O={vela_fechada['open']} | "
                f"C={vela_fechada['close']}"
            )

    # ------------------------------------------------------------
    # 2. RSI
    # ------------------------------------------------------------

    closes = [
        c["close"]
        for c in historico
        if c["timestamp"] <= ts
    ]

    rsi = calcular_rsi(
        closes,
        RSI_PERIODO
    )

    if rsi is None:

        log.info(
            "Histórico insuficiente para RSI."
        )

        return

    sinal = gerar_sinal(
        rsi
    )

    log.info(
        f"🔎 RSI | "
        f"{vela_fechada['datetime']} | "
        f"RSI={rsi:.2f} | "
        f"Sinal={sinal}"
    )

    # ------------------------------------------------------------
    # 3. NÃO HÁ SINAL
    # ------------------------------------------------------------

    if sinal not in (
        "CALL",
        "PUT"
    ):

        return

    # ------------------------------------------------------------
    # 4. PRECISAMOS DA PRÓXIMA VELA
    # ------------------------------------------------------------

    if vela_entrada is None:

        log.warning(
            "Sinal detectado, mas ainda não existe "
            "vela de entrada."
        )

        return

    # ------------------------------------------------------------
    # 5. CRIA SINAL
    # ------------------------------------------------------------

    criado = criar_sinal_simulado(
        aba_sinais,
        vela_fechada,
        vela_entrada,
        rsi,
        sinal,
        sinais
    )

    if criado:

        # Recarrega imediatamente para obter a linha correta
        # criada pelo Google Sheets.
        sinais_atualizados = carregar_sinais(
            aba_sinais
        )

        sinais.clear()

        sinais.update(
            sinais_atualizados
        )


# ================================================================
# MAIN
# ================================================================

def main():

    log.info("")
    log.info("=" * 70)
    log.info("IQ OPTION BOT V1.1")
    log.info("=" * 70)
    log.info(f"PAR: {PAR}")
    log.info("TIMEFRAME: M1")
    log.info("ESTRATÉGIA: RSI 30/70")
    log.info("MODO: SIMULADOR")
    log.info("=" * 70)

    validar_configuracao()

    # ============================================================
    # GOOGLE
    # ============================================================

    spreadsheet = conectar_google()

    aba_coletas = obter_aba(
        spreadsheet,
        ABA_COLETAS
    )

    aba_sinais = obter_aba(
        spreadsheet,
        ABA_SINAIS
    )

    aba_resumo = obter_aba(
        spreadsheet,
        ABA_RESUMO
    )

    garantir_cabecalho(
        aba_sinais,
        HEADER_SINAIS
    )

    garantir_cabecalho(
        aba_resumo,
        HEADER_RESUMO
    )

    timestamps_coletas = (
        carregar_timestamps_coletas(
            aba_coletas
        )
    )

    sinais = carregar_sinais(
        aba_sinais
    )

    log.info(
        f"Candles já registrados: "
        f"{len(timestamps_coletas)}"
    )

    log.info(
        f"Sinais já registrados: "
        f"{len(sinais)}"
    )

    # ============================================================
    # IQ OPTION
    # ============================================================

    api = conectar_iq()

    # ============================================================
    # HISTÓRICO
    # ============================================================

    historico = obter_historico_inicial(
        api
    )

    if len(historico) < RSI_PERIODO + 2:

        raise RuntimeError(
            "Histórico insuficiente para iniciar o RSI."
        )

    # ============================================================
    # STREAM
    # ============================================================

    iniciar_stream(
        api
    )

    telegram_online()

    # ============================================================
    # SNAPSHOT INICIAL
    # ============================================================

    ultimo_timestamp_processado = None

    snapshot = obter_stream_candles(
        api
    )

    if snapshot:

        ultimo_timestamp_processado = (
            snapshot[-1]["timestamp"]
        )

        log.info(
            f"Última vela inicialmente detectada: "
            f"{snapshot[-1]['datetime']}"
        )

    # ============================================================
    # LOOP
    # ============================================================

    contador_heartbeat = 0

    while True:

        try:

            # ====================================================
            # CONEXÃO
            # ====================================================

            if not api.check_connect():

                log.warning(
                    "⚠️ IQ Option desconectada."
                )

                try:

                    api.connect()

                    api.change_balance(
                        "PRACTICE"
                    )

                    iniciar_stream(
                        api
                    )

                    telegram_reconectado()

                except Exception as e:

                    log.error(
                        f"Falha na reconexão: {e}"
                    )

                    time.sleep(10)

                    continue

            # ====================================================
            # STREAM
            # ====================================================

            candles_stream = (
                obter_stream_candles(api)
            )

            if not candles_stream:

                time.sleep(
                    LOOP_SECONDS
                )

                continue

            timestamp_atual = (
                candles_stream[-1]["timestamp"]
            )

            if ultimo_timestamp_processado is None:

                ultimo_timestamp_processado = (
                    timestamp_atual
                )

                time.sleep(
                    LOOP_SECONDS
                )

                continue

            # ====================================================
            # NOVA VELA
            # ====================================================

            if timestamp_atual > ultimo_timestamp_processado:

                mapa = {
                    c["timestamp"]: c
                    for c in candles_stream
                }

                # ------------------------------------------------
                # A vela que acabou de fechar.
                # ------------------------------------------------

                ts_fechada = (
                    timestamp_atual -
                    TIMEFRAME
                )

                vela_fechada = mapa.get(
                    ts_fechada
                )

                # ------------------------------------------------
                # A vela atualmente em formação.
                # Ela será a vela de entrada para um sinal
                # gerado pela vela fechada.
                # ------------------------------------------------

                vela_entrada = mapa.get(
                    timestamp_atual
                )

                if vela_fechada:

                    # =================================================
                    # PRIMEIRO:
                    # FECHA SINAIS QUE TINHAM ESSA VELA COMO ENTRADA
                    # =================================================

                    sinais = carregar_sinais(
                        aba_sinais
                    )

                    resultados = atualizar_resultados(
                        aba_sinais,
                        sinais,
                        vela_fechada
                    )

                    if resultados > 0:

                        sinais = carregar_sinais(
                            aba_sinais
                        )

                        resumo = atualizar_resumo(
                            aba_resumo,
                            sinais
                        )

                        log.info(
                            "📈 PLACAR | "
                            f"Sinais={resumo['total']} | "
                            f"W={resumo['wins']} | "
                            f"L={resumo['losses']} | "
                            f"Emp={resumo['empates']} | "
                            f"Assertividade="
                            f"{resumo['assertividade']:.2f}% | "
                            f"Saldo={resumo['saldo']:+d} | "
                            f"Maior LOSS="
                            f"{resumo['maior_loss']}"
                        )

                    # =================================================
                    # SEGUNDO:
                    # ADICIONA A VELA AO HISTÓRICO
                    # =================================================

                    historico = [
                        c
                        for c in historico
                        if c["timestamp"]
                        != vela_fechada["timestamp"]
                    ]

                    historico.append(
                        vela_fechada
                    )

                    historico.sort(
                        key=lambda x: x["timestamp"]
                    )

                    historico = historico[
                        -HISTORICO_CANDLES:
                    ]

                    # =================================================
                    # TERCEIRO:
                    # SALVA VELA + CALCULA RSI + GERA NOVO SINAL
                    # =================================================

                    processar_vela_fechada(
                        vela_fechada,
                        vela_entrada,
                        historico,
                        aba_coletas,
                        aba_sinais,
                        timestamps_coletas,
                        sinais
                    )

                    # =================================================
                    # QUARTO:
                    # PLACAR
                    # =================================================

                    sinais = carregar_sinais(
                        aba_sinais
                    )

                    resumo = atualizar_resumo(
                        aba_resumo,
                        sinais
                    )

                    log.info(
                        "📈 PLACAR | "
                        f"Sinais={resumo['total']} | "
                        f"W={resumo['wins']} | "
                        f"L={resumo['losses']} | "
                        f"Emp={resumo['empates']} | "
                        f"Assertividade="
                        f"{resumo['assertividade']:.2f}% | "
                        f"Saldo={resumo['saldo']:+d} | "
                        f"Maior LOSS="
                        f"{resumo['maior_loss']}"
                    )

                # ------------------------------------------------
                # Atualiza última vela processada
                # ------------------------------------------------

                ultimo_timestamp_processado = (
                    timestamp_atual
                )

            # ====================================================
            # HEARTBEAT
            # ====================================================

            contador_heartbeat += 1

            if contador_heartbeat >= 60:

                contador_heartbeat = 0

                sinais = carregar_sinais(
                    aba_sinais
                )

                resumo = calcular_resumo(
                    sinais
                )

                log.info(
                    "💓 HEARTBEAT | "
                    f"{PAR} M1 | "
                    f"stream=OK | "
                    f"última vela="
                    f"{formatar_datetime(timestamp_atual)} | "
                    f"RSI estratégia=30/70 | "
                    f"W={resumo['wins']} "
                    f"L={resumo['losses']}"
                )

            time.sleep(
                LOOP_SECONDS
            )

        except KeyboardInterrupt:

            log.info(
                "Bot encerrado."
            )

            try:

                api.stop_candles_stream(
                    PAR,
                    TIMEFRAME
                )

            except Exception:
                pass

            break

        except Exception as e:

            log.exception(
                f"Erro no loop principal: {e}"
            )

            time.sleep(10)


# ================================================================
# EXECUÇÃO
# ================================================================

if __name__ == "__main__":
    main()
