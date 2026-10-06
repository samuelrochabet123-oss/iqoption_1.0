# ================================================================
# IQ OPTION BOT V1.0
# ================================================================
#
# MODO: SIMULADOR
# ATIVO: definido pela variável PAR
# TIMEFRAME: M1
# ESTRATÉGIA: RSI 14 - 30/70
#
# FLUXO:
#
# IQ OPTION
#     ↓
# STREAM DE CANDLES
#     ↓
# VELA FECHADA
#     ↓
# RSI 14
#     ↓
# CALL / PUT
#     ↓
# ENTRADA NA ABERTURA DA PRÓXIMA VELA
#     ↓
# FECHAMENTO DA PRÓXIMA VELA
#     ↓
# WIN / LOSS / EMPATE
#     ↓
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

# Horário oficial para exibição na planilha/Telegram.
# O timestamp da IQ Option continua sendo usado internamente
# para identificar as velas.
TZ_LOCAL = ZoneInfo("America/Sao_Paulo")

LOOP_SECONDS = 1

# Quantidade de candles necessários para RSI.
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


def timestamp_da_vela(candle):
    return int(candle["from"])


def normalizar_candle(timestamp, candle):
    """
    Converte o formato retornado pelo stream para um formato
    interno padronizado.
    """

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
# VALIDAÇÃO DAS VARIÁVEIS
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
                "A1",
                [cabecalho]
            )

            return

        # Não altera abas existentes com dados.
        # As abas novas recebem o cabeçalho.
        if len(primeira_linha) == 1 and not primeira_linha[0]:

            aba.update(
                "A1",
                [cabecalho]
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

        return

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

    except Exception as e:

        log.warning(
            f"Erro enviando Telegram: {e}"
        )


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
    # GARANTIA DE SEGURANÇA:
    # O BOT SEMPRE USA PRACTICE.
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
# STREAM -> LISTA ORDENADA
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
# HISTÓRICO INICIAL
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
# GOOGLE - CONTROLE DE CANDLES
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

        if texto == "datetime":

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
                int(
                    dt.timestamp()
                )
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
        candle["close"],
        candle["volume"],
        "",
        "",
        "",
        "",
        ""
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
# SINAIS - CARREGAMENTO
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
# CRIAÇÃO DO SINAL
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
            f"Sinal: {candle_fechado['datetime']}"
        )

        log.info(
            f"Entrada: {candle_entrada['datetime']}"
        )

        log.info(
            f"Preço entrada: {candle_entrada['open']}"
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
            f"Entrada:\n"
            f"{candle_entrada['datetime']}\n"
            f"Preço: {candle_entrada['open']}\n\n"
            "⏳ Aguardando fechamento..."
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
# ATUALIZA RESULTADO DE SINAIS ABERTOS
# ================================================================

def atualizar_resultados(
    aba_sinais,
    sinais,
    candles
):

    if not sinais:

        return

    mapa = {
        c["timestamp"]: c
        for c in candles
    }

    alterados = 0

    for dt_sinal, sinal in list(
        sinais.items()
    ):

        if sinal["status"] != "ABERTO":

            continue

        try:

            # O sinal foi gerado pela vela N.
            #
            # A entrada é na abertura da vela N+1.
            # O resultado é no fechamento da vela N+1.
            #
            # Portanto precisamos encontrar a vela cujo
            # timestamp seja exatamente +60 segundos.

            dt_sinal_obj = datetime.strptime(
                sinal["datetime_sinal"],
                "%Y-%m-%d %H:%M:%S"
            )

            dt_sinal_obj = dt_sinal_obj.replace(
                tzinfo=TZ_LOCAL
            )

            ts_sinal = int(
                dt_sinal_obj.timestamp()
            )

            ts_resultado = (
                ts_sinal + TIMEFRAME
            )

            candle_resultado = mapa.get(
                ts_resultado
            )

            if candle_resultado is None:

                continue

            entrada = float(
                sinal["entrada"]
            )

            saida = float(
                candle_resultado["close"]
            )

            resultado = determinar_resultado(
                sinal["sinal"],
                entrada,
                saida
            )

            # ----------------------------------------------------
            # Atualiza a linha
            # ----------------------------------------------------

            linha = sinal["linha"]

            aba_sinais.update_cell(
                linha,
                8,
                candle_resultado["datetime"]
            )

            aba_sinais.update_cell(
                linha,
                9,
                saida
            )

            aba_sinais.update_cell(
                linha,
                10,
                resultado
            )

            aba_sinais.update_cell(
                linha,
                12,
                "FECHADO"
            )

            sinal["datetime_resultado"] = (
                candle_resultado["datetime"]
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
                f"{emoji} RESULTADO | "
                f"{sinal['sinal']} | "
                f"{resultado} | "
                f"Entrada={entrada} | "
                f"Saída={saida}"
            )

            telegram_enviar(
                f"{emoji} RESULTADO — SIMULADOR\n\n"
                f"Par: {PAR}\n"
                f"Estratégia: RSI 30/70\n"
                f"RSI: {sinal['rsi']}\n"
                f"Sinal: {sinal['sinal']}\n\n"
                f"Entrada: {entrada}\n"
                f"Saída: {saida}\n"
                f"Resultado: {resultado}"
            )

        except Exception as e:

            log.warning(
                f"Erro atualizando sinal "
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

    # Ordena pelo horário do sinal
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

        # A V1 possui apenas uma estratégia.
        # Atualizamos A2:I2 sem apagar a planilha.

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
# STATUS TELEGRAM
# ================================================================

def telegram_online():

    telegram_enviar(
        "🤖 IQ OPTION BOT V1\n\n"
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
        "🔄 IQ OPTION BOT V1\n\n"
        "Conexão restabelecida.\n\n"
        f"Par: {PAR}\n"
        "Modo: SIMULADOR"
    )


# ================================================================
# PROCESSAMENTO DE VELAS FECHADAS
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
    # 1. SALVA A VELA NA PLANILHA
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
    # 2. CALCULA RSI
    #
    # O RSI usa somente candles FECHADOS.
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
    # 3. SÓ CRIA SINAL SE CALL OU PUT
    # ------------------------------------------------------------

    if sinal not in (
        "CALL",
        "PUT"
    ):

        return

    # ------------------------------------------------------------
    # 4. PRÓXIMA VELA É A ENTRADA
    # ------------------------------------------------------------

    if vela_entrada is None:

        log.warning(
            "Não existe vela de entrada ainda."
        )

        return

    criado = criar_sinal_simulado(
        aba_sinais,
        vela_fechada,
        vela_entrada,
        rsi,
        sinal,
        sinais
    )

    if criado:

        sinais[
            vela_fechada["datetime"]
        ] = {
            "linha": None,
            "datetime_sinal":
                vela_fechada["datetime"],
            "par": PAR,
            "estrategia": "RSI 30/70",
            "rsi": round(rsi, 2),
            "sinal": sinal,
            "datetime_entrada":
                vela_entrada["datetime"],
            "entrada":
                vela_entrada["open"],
            "datetime_resultado": "",
            "saida": "",
            "resultado": "AGUARDANDO",
            "saldo_wl": "",
            "status": "ABERTO"
        }

        # Depois da inclusão, recarregaremos a planilha
        # periodicamente para obter o número real da linha.


# ================================================================
# MAIN
# ================================================================

def main():

    log.info("")
    log.info("=" * 70)
    log.info("IQ OPTION BOT V1.0")
    log.info("=" * 70)
    log.info(f"PAR: {PAR}")
    log.info("TIMEFRAME: M1")
    log.info("ESTRATÉGIA: RSI 30/70")
    log.info("MODO: SIMULADOR")
    log.info("=" * 70)

    validar_configuracao()

    # ------------------------------------------------------------
    # GOOGLE
    # ------------------------------------------------------------

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

    # ------------------------------------------------------------
    # IQ OPTION
    # ------------------------------------------------------------

    api = conectar_iq()

    # ------------------------------------------------------------
    # HISTÓRICO PARA RSI
    # ------------------------------------------------------------

    historico = obter_historico_inicial(
        api
    )

    if len(historico) < RSI_PERIODO + 2:

        raise RuntimeError(
            "Histórico insuficiente para iniciar o RSI."
        )

    # ------------------------------------------------------------
    # STREAM
    # ------------------------------------------------------------

    iniciar_stream(
        api
    )

    telegram_online()

    # ------------------------------------------------------------
    # CONTROLE DA ÚLTIMA VELA
    # ------------------------------------------------------------

    ultimo_timestamp_processado = None

    # Primeiro snapshot do stream.
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

    # ------------------------------------------------------------
    # LOOP
    # ------------------------------------------------------------

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

            # ----------------------------------------------------
            # Descobre a vela atualmente em formação.
            #
            # Se o stream já possui uma vela com timestamp maior
            # que a última processada, então a última processada
            # fechou.
            # ----------------------------------------------------

            timestamp_atual = (
                candles_stream[-1]["timestamp"]
            )

            if (
                ultimo_timestamp_processado
                is None
            ):

                ultimo_timestamp_processado = (
                    timestamp_atual
                )

                time.sleep(
                    LOOP_SECONDS
                )

                continue

            # ====================================================
            # NOVA VELA DETECTADA
            # ====================================================

            if timestamp_atual > ultimo_timestamp_processado:

                # Procuramos a vela que acabou de fechar.
                #
                # Exemplo:
                #
                # última processada = 14:30
                # nova atual          = 14:31
                #
                # 14:30 acabou de fechar.
                # 14:31 é a vela de entrada.
                #

                mapa = {
                    c["timestamp"]: c
                    for c in candles_stream
                }

                ts_fechada = (
                    timestamp_atual -
                    TIMEFRAME
                )

                vela_fechada = mapa.get(
                    ts_fechada
                )

                vela_entrada = mapa.get(
                    timestamp_atual
                )

                if vela_fechada:

                    # --------------------------------------------
                    # Adiciona ao histórico local
                    # --------------------------------------------

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

                    # Mantém somente o necessário
                    historico = historico[
                        -HISTORICO_CANDLES:
                    ]

                    # --------------------------------------------
                    # PROCESSA
                    # --------------------------------------------

                    processar_vela_fechada(
                        vela_fechada,
                        vela_entrada,
                        historico,
                        aba_coletas,
                        aba_sinais,
                        timestamps_coletas,
                        sinais
                    )

                # ------------------------------------------------
                # A nova vela passa a ser a última processada.
                # ------------------------------------------------

                ultimo_timestamp_processado = (
                    timestamp_atual
                )

                # ------------------------------------------------
                # Atualiza resultados dos sinais.
                #
                # Neste ponto, uma vela nova começou.
                # Portanto a vela anterior fechou.
                # ------------------------------------------------

                # Recarregamos os sinais para garantir que
                # temos os números corretos das linhas.
                sinais = carregar_sinais(
                    aba_sinais
                )

                atualizar_resultados(
                    aba_sinais,
                    sinais,
                    candles_stream
                )

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

            # ====================================================
            # HEARTBEAT
            # ====================================================

            contador_heartbeat += 1

            if contador_heartbeat >= 60:

                contador_heartbeat = 0

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