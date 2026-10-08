# ================================================================
# IQ OPTION BOT V1.3
# PRICE ACTION + EXAUSTÃO + S/R + COOLDOWN
#
# MODO:
#   PAPER TRADING / SIMULADOR
#
# MERCADO ANALISADO:
#   EUR/USD
#
# TIMEFRAME:
#   M1
#
# MODELO:
#   OPERAÇÃO BINÁRIA/DIGITAL SIMULADA
#
# DIREÇÕES:
#   COMPRA
#   VENDA
#
# EXPIRAÇÃO:
#   1 MINUTO
#
# NÃO ENVIA ORDENS REAIS
# ================================================================

import os
import time
import json
import logging
import threading

from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
import gspread

from google.oauth2.service_account import Credentials
from iqoptionapi.stable_api import IQ_Option


# ================================================================
# CONFIGURAÇÕES
# ================================================================

PAR = os.getenv("PAR", "EURUSD").upper()

# 60 segundos = M1
TIMEFRAME = 60

# Quantidade de candles usadas no histórico
HISTORICO_CANDLES = 100

# Suporte / resistência
SR_PERIODO = 20

# Exaustão
EXAUSTAO_FATOR_TAMANHO = 1.5

# Pavio mínimo em relação ao tamanho total da vela
MIN_PAVIO_RATIO = 0.35

# Cooldown entre sinais
COOLDOWN_VELAS = 3

# Quantidade de candles mantidos no stream
STREAM_MAXDICT = 20

# ================================================================
# EXPIRAÇÃO DA OPERAÇÃO
# ================================================================

EXPIRACAO_SEGUNDOS = TIMEFRAME

# ================================================================
# ESTADO
# ================================================================

ultimo_sinal_timestamp = 0

# ================================================================
# GOOGLE SHEETS
# ================================================================

GOOGLE_SHEET_ID = os.getenv(
    "GOOGLE_SHEET_ID",
    "1uuw_jS5-e4dUQ28DCffknMaMnJfUbekHLkTFp0aqGtA"
)

ABA_COLETAS = "IQOption_Coletas"
ABA_SINAIS = "Sinais_Bot"
ABA_RESUMO = "Resumo"

# ================================================================
# CREDENCIAIS
# ================================================================

IQ_EMAIL = os.getenv("IQ_EMAIL")
IQ_PASSWORD = os.getenv("IQ_PASSWORD")

GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ================================================================
# FUSO
# ================================================================

TZ_LOCAL = ZoneInfo("America/Sao_Paulo")


# ================================================================
# LOG
# ================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("IQOPTION-BOT-V1.3")


# ================================================================
# SERVIDOR HTTP
# Render Health Check
# ================================================================

class DummyHTTPHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-type",
            "text/html; charset=utf-8"
        )

        self.end_headers()

        mensagem = (
            "IQ Option Bot V1.3 "
            "(Paper Trading / Binario M1) ONLINE"
        )

        self.wfile.write(
            mensagem.encode("utf-8")
        )

    def log_message(self, format, *args):
        return


def iniciar_servidor_http():

    porta = int(
        os.getenv("PORT", 8080)
    )

    servidor = HTTPServer(
        ("0.0.0.0", porta),
        DummyHTTPHandler
    )

    log.info(
        f"Servidor HTTP ativo na porta {porta}"
    )

    servidor.serve_forever()


# ================================================================
# CABEÇALHOS GOOGLE SHEETS
# ================================================================

HEADER_COLETAS = [
    "datetime",
    "par",
    "open",
    "high",
    "low",
    "close",
    "volume"
]


HEADER_SINAIS = [
    "datetime_sinal",
    "par",
    "estrategia",
    "pavio_ratio",
    "direcao",
    "datetime_entrada",
    "preco_entrada",
    "datetime_expiracao",
    "preco_expiracao",
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
# UTILITÁRIOS E SINCRONIZAÇÃO DE TEMPO
# ================================================================

def smart_sleep_proximo_minuto():
    """Garante que o script 'acorde' exatamente no segundo 00.050 do próximo minuto."""
    agora = datetime.now()
    segundos_espera = 60 - agora.second - (agora.microsecond / 1_000_000.0) + 0.050
    if segundos_espera > 0:
        time.sleep(segundos_espera)


def para_float(valor):

    if valor is None or valor == "":
        return 0.0

    if isinstance(valor, (int, float)):
        return float(valor)

    return float(
        str(valor)
        .replace(",", ".")
        .strip()
    )


def timestamp_para_local(timestamp):

    return datetime.fromtimestamp(
        int(timestamp),
        tz=timezone.utc
    ).astimezone(TZ_LOCAL)


def formatar_datetime(timestamp):

    return timestamp_para_local(
        timestamp
    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def normalizar_candle(timestamp, candle):

    return {
        "timestamp": int(timestamp),

        "datetime": formatar_datetime(
            timestamp
        ),

        "open": float(
            candle["open"]
        ),

        "high": float(
            candle["max"]
        ),

        "low": float(
            candle["min"]
        ),

        "close": float(
            candle["close"]
        ),

        "volume": float(
            candle.get("volume", 0)
        )
    }


# ================================================================
# VALIDAÇÃO
# ================================================================

def validar_configuracao():

    obrigatorias = {

        "IQ_EMAIL":
            IQ_EMAIL,

        "IQ_PASSWORD":
            IQ_PASSWORD,

        "GOOGLE_CREDENTIALS_JSON":
            GOOGLE_CREDENTIALS_JSON,

        "TELEGRAM_BOT_TOKEN":
            TELEGRAM_BOT_TOKEN,

        "TELEGRAM_CHAT_ID":
            TELEGRAM_CHAT_ID
    }

    faltando = [
        nome
        for nome, valor
        in obrigatorias.items()
        if not valor
    ]

    if faltando:

        raise RuntimeError(
            "Variáveis de ambiente ausentes: "
            + ", ".join(faltando)
        )


# ================================================================
# GOOGLE SHEETS
# ================================================================

def conectar_google():

    log.info(
        "Conectando ao Google Sheets..."
    )

    info = json.loads(
        GOOGLE_CREDENTIALS_JSON
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
        f"Planilha conectada: "
        f"{spreadsheet.title}"
    )

    return spreadsheet


def obter_aba(
    spreadsheet,
    nome
):

    try:

        return spreadsheet.worksheet(
            nome
        )

    except gspread.WorksheetNotFound:

        log.info(
            f"Criando aba '{nome}'..."
        )

        return spreadsheet.add_worksheet(
            title=nome,
            rows=5000,
            cols=20
        )


def garantir_cabecalho(
    aba,
    cabecalho
):

    try:

        if not aba.row_values(1):

            aba.update(
                range_name="A1",
                values=[cabecalho]
            )

    except Exception as e:

        log.warning(
            f"Erro verificando cabeçalho "
            f"{aba.title}: {e}"
        )


# ================================================================
# TELEGRAM
# ================================================================

def telegram_enviar(
    mensagem
):

    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):
        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
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

        return resposta.status_code == 200

    except Exception as e:

        log.warning(
            f"Erro enviando Telegram: {e}"
        )

        return False


# ================================================================
# ESTRATÉGIA
#
# PRICE ACTION
# +
# EXAUSTÃO
# +
# SUPORTE / RESISTÊNCIA
# ================================================================

def calcular_suporte_resistencia(
    historico,
    periodo=20
):

    if len(historico) < periodo:

        return None, None

    velas_recentes = (
        historico[-periodo:]
    )

    suporte = min(
        c["low"]
        for c in velas_recentes
    )

    resistencia = max(
        c["high"]
        for c in velas_recentes
    )

    return suporte, resistencia


def gerar_sinal_estrategia(
    historico,
    timestamp_atual
):

    global ultimo_sinal_timestamp

    if len(historico) < (
        SR_PERIODO + 10
    ):

        return "NEUTRO", None

    if (
        timestamp_atual
        - ultimo_sinal_timestamp
        <
        (
            COOLDOWN_VELAS
            * TIMEFRAME
        )
    ):

        return "NEUTRO", None

    suporte, resistencia = (
        calcular_suporte_resistencia(
            historico[:-1],
            SR_PERIODO
        )
    )

    if (
        suporte is None
        or resistencia is None
    ):

        return "NEUTRO", None

    vela_atual = historico[-1]

    velas_anteriores = (
        historico[-11:-1]
    )

    abertura = vela_atual["open"]
    fechamento = vela_atual["close"]

    maxima = vela_atual["high"]
    minima = vela_atual["low"]

    tamanho_total = (
        maxima - minima
    )

    if tamanho_total == 0:

        return "NEUTRO", None

    tamanhos_anteriores = [
        c["high"] - c["low"]
        for c in velas_anteriores
    ]

    if tamanhos_anteriores:

        media_tamanho = (
            sum(tamanhos_anteriores)
            /
            len(tamanhos_anteriores)
        )

    else:

        media_tamanho = 0.0001

    eh_exaustao = (
        tamanho_total
        >=
        (
            media_tamanho
            *
            EXAUSTAO_FATOR_TAMANHO
        )
    )

    pavio_superior = (
        maxima
        -
        max(
            abertura,
            fechamento
        )
    )

    pavio_inferior = (
        min(
            abertura,
            fechamento
        )
        -
        minima
    )

    ratio_pavio_sup = (
        pavio_superior
        /
        tamanho_total
    )

    ratio_pavio_inf = (
        pavio_inferior
        /
        tamanho_total
    )

    if (
        eh_exaustao
        and maxima >= resistencia
        and fechamento > abertura
        and ratio_pavio_sup
        >= MIN_PAVIO_RATIO
    ):

        ultimo_sinal_timestamp = (
            timestamp_atual
        )

        return (
            "VENDA",
            round(
                ratio_pavio_sup * 100,
                2
            )
        )

    if (
        eh_exaustao
        and minima <= suporte
        and fechamento < abertura
        and ratio_pavio_inf
        >= MIN_PAVIO_RATIO
    ):

        ultimo_sinal_timestamp = (
            timestamp_atual
        )

        return (
            "COMPRA",
            round(
                ratio_pavio_inf * 100,
                2
            )
        )

    return (
        "NEUTRO",
        round(
            max(
                ratio_pavio_sup,
                ratio_pavio_inf
            ) * 100,
            2
        )
    )


# ================================================================
# IQ OPTION
# ================================================================

def conectar_iq():

    log.info(
        f"Conectando IQ Option | "
        f"PAR={PAR}"
    )

    api = IQ_Option(
        IQ_EMAIL,
        IQ_PASSWORD
    )

    try:

        api.set_max_reconnect(-1)

    except Exception:

        pass

    conectado, motivo = (
        api.connect()
    )

    if not conectado:

        raise RuntimeError(
            f"Falha na conexão IQ Option: "
            f"{motivo}"
        )

    api.change_balance(
        "PRACTICE"
    )

    log.info(
        "IQ Option conectada | "
        "MODO PAPER TRADING"
    )

    return api


def iniciar_stream(api):

    api.start_candles_stream(
        PAR,
        TIMEFRAME,
        STREAM_MAXDICT
    )

    time.sleep(2)


def obter_stream_candles(api):

    # COLETA DIRETA DE ALTA PRECISÃO NO SEGUNDO ZERO EXATO
    try:

        dados_historico = api.get_candles(
            PAR,
            TIMEFRAME,
            3,
            int(time.time())
        )

        if dados_historico and len(dados_historico) >= 2:

            candles = [
                normalizar_candle(c["from"], c)
                for c in dados_historico
                if "from" in c
            ]

            candles.sort(key=lambda x: x["timestamp"])

            return candles

    except Exception as e:

        log.warning(f"Erro na coleta precisa via API: {e}")

    dados = api.get_realtime_candles(
        PAR,
        TIMEFRAME
    )

    if not dados:

        return []

    candles = []

    for timestamp, candle in dados.items():

        try:

            if "open" in candle:

                candles.append(
                    normalizar_candle(
                        timestamp,
                        candle
                    )
                )

        except Exception:

            pass

    candles.sort(
        key=lambda x:
        x["timestamp"]
    )

    return candles


def obter_historico_inicial(api):

    try:

        dados = api.get_candles(
            PAR,
            TIMEFRAME,
            HISTORICO_CANDLES,
            int(time.time())
        )

        candles = [
            normalizar_candle(
                c["from"],
                c
            )
            for c in dados
            if "from" in c
        ]

        candles.sort(
            key=lambda x:
            x["timestamp"]
        )

        return candles

    except Exception as e:

        log.warning(
            f"Erro obtendo histórico: {e}"
        )

        return []


# ================================================================
# GOOGLE SHEETS - COLETAS
# ================================================================

def carregar_timestamps_coletas(
    aba
):

    try:

        valores = aba.col_values(1)

        timestamps = set()

        for valor in valores[1:]:

            if (
                not valor
                or valor.lower()
                == "datetime"
            ):
                continue

            try:

                dt = (
                    datetime
                    .strptime(
                        valor.strip(),
                        "%Y-%m-%d %H:%M:%S"
                    )
                    .replace(
                        tzinfo=TZ_LOCAL
                    )
                )

                timestamps.add(
                    int(
                        dt.timestamp()
                    )
                )

            except Exception:

                continue

        return timestamps

    except Exception:

        return set()


def registrar_candle(
    aba,
    candle
):

    linha = [

        candle["datetime"],

        PAR,

        candle["open"],

        candle["high"],

        candle["low"],

        candle["close"],

        candle["volume"]
    ]

    try:

        aba.append_row(
            linha,
            value_input_option=
            "USER_ENTERED"
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

def carregar_sinais(
    aba
):

    try:

        valores = aba.get_all_values()

        sinais = {}

        for numero_linha, row in enumerate(
            valores[1:],
            start=2
        ):

            if len(row) < 12:
                continue

            sinais[row[0]] = {

                "linha":
                    numero_linha,

                "datetime_sinal":
                    row[0],

                "par":
                    row[1],

                "estrategia":
                    row[2],

                "pavio_ratio":
                    row[3],

                "direcao":
                    row[4],

                "datetime_entrada":
                    row[5],

                "preco_entrada":
                    row[6],

                "datetime_expiracao":
                    row[7],

                "preco_expiracao":
                    row[8],

                "resultado":
                    row[9],

                "saldo_wl":
                    row[10],

                "status":
                    row[11]
            }

        return sinais

    except Exception as e:

        log.warning(
            f"Erro carregando sinais: {e}"
        )

        return {}


# ================================================================
# CRIAÇÃO DO SINAL
# ================================================================

def criar_sinal_simulado(
    aba,
    candle_sinal,
    candle_entrada,
    pavio_ratio,
    direcao,
    sinais_existentes
):

    datetime_sinal = (
        candle_sinal["datetime"]
    )

    if (
        datetime_sinal
        in sinais_existentes
    ):

        return False

    preco_entrada = (
        candle_entrada["open"]
    )

    timestamp_entrada = (
        candle_entrada["timestamp"]
    )

    timestamp_expiracao = (
        timestamp_entrada
        + EXPIRACAO_SEGUNDOS
    )

    datetime_entrada = (
        candle_entrada["datetime"]
    )

    datetime_expiracao = (
        formatar_datetime(
            timestamp_expiracao
        )
    )

    linha = [

        datetime_sinal,

        PAR,

        "Price Action + Exaustao",

        f"{pavio_ratio}%",

        direcao,

        datetime_entrada,

        preco_entrada,

        datetime_expiracao,

        "",

        "AGUARDANDO",

        "",

        "ABERTO"
    ]

    try:

        aba.append_row(
            linha,
            value_input_option=
            "USER_ENTERED"
        )

        log.info("=" * 60)

        log.info(
            f"🚨 NOVO SINAL | "
            f"{direcao}"
        )

        log.info(
            f"PAR: {PAR}"
        )

        log.info(
            f"ENTRADA: "
            f"{datetime_entrada}"
        )

        log.info(
            f"PREÇO ENTRADA: "
            f"{preco_entrada}"
        )

        log.info(
            f"EXPIRAÇÃO: "
            f"{datetime_expiracao}"
        )

        log.info(
            f"PAVIO: "
            f"{pavio_ratio}%"
        )

        log.info("=" * 60)

        telegram_enviar(
            "🚨 NOVO SINAL M1\n\n"
            f"Par: {PAR}\n"
            f"Direção: {direcao}\n"
            f"Entrada: "
            f"{datetime_entrada}\n"
            f"Preço: "
            f"{preco_entrada}\n"
            f"Expiração: "
            f"{datetime_expiracao}\n"
            f"Pavio: "
            f"{pavio_ratio}%"
        )

        return True

    except Exception as e:

        log.warning(
            f"Erro criando sinal: {e}"
        )

        return False


# ================================================================
# RESULTADO DA OPERAÇÃO
# ================================================================

def determinar_resultado(
    direcao,
    entrada,
    saida
):

    entrada = para_float(
        entrada
    )

    saida = para_float(
        saida
    )

    if saida == entrada:

        return "EMPATE"

    if direcao == "COMPRA":

        if saida > entrada:

            return "WIN"

        return "LOSS"

    if direcao == "VENDA":

        if saida < entrada:

            return "WIN"

        return "LOSS"

    return "EMPATE"


# ================================================================
# ATUALIZAR RESULTADOS
# ================================================================

def atualizar_resultados(
    aba_sinais,
    sinais,
    candle_fechado
):

    if not sinais:

        return 0

    alterados = 0

    ts_fechado = (
        candle_fechado["timestamp"]
    )

    preco_saida = (
        candle_fechado["close"]
    )

    for dt_sinal, sinal in list(
        sinais.items()
    ):

        if (
            sinal.get("status")
            != "ABERTO"
        ):

            continue

        try:

            dt_entrada = (
                datetime
                .strptime(
                    sinal[
                        "datetime_entrada"
                    ],
                    "%Y-%m-%d %H:%M:%S"
                )
                .replace(
                    tzinfo=TZ_LOCAL
                )
            )

            ts_entrada = int(
                dt_entrada.timestamp()
            )

            ts_expiracao = (
                ts_entrada
                + EXPIRACAO_SEGUNDOS
            )

            if (
                ts_fechado
                >= ts_expiracao
            ):

                entrada = para_float(
                    sinal[
                        "preco_entrada"
                    ]
                )

                saida = para_float(
                    preco_saida
                )

                resultado = (
                    determinar_resultado(
                        sinal["direcao"],
                        entrada,
                        saida
                    )
                )

                linha = sinal[
                    "linha"
                ]

                if not linha:

                    continue

                saldo_wl = {
                    "WIN": 1,
                    "LOSS": -1,
                    "EMPATE": 0
                }.get(
                    resultado,
                    0
                )

                aba_sinais.update(
                    range_name=
                    f"H{linha}:L{linha}",

                    values=[[
                        candle_fechado[
                            "datetime"
                        ],

                        saida,

                        resultado,

                        saldo_wl,

                        "FECHADO"
                    ]],

                    value_input_option=
                    "USER_ENTERED"
                )

                sinal[
                    "status"
                ] = "FECHADO"

                sinal[
                    "resultado"
                ] = resultado

                sinal[
                    "saldo_wl"
                ] = saldo_wl

                alterados += 1

                emoji = {

                    "WIN": "✅",

                    "LOSS": "❌",

                    "EMPATE": "⚪"

                }.get(
                    resultado,
                    "⚪"
                )

                log.info(
                    "=" * 60
                )

                log.info(
                    f"{emoji} "
                    f"RESULTADO | "
                    f"{resultado}"
                )

                log.info(
                    f"Par: {PAR}"
                )

                log.info(
                    f"Direção: "
                    f"{sinal['direcao']}"
                )

                log.info(
                    f"Entrada: "
                    f"{entrada}"
                )

                log.info(
                    f"Expiração: "
                    f"{saida}"
                )

                log.info(
                    "=" * 60
                )

                telegram_enviar(
                    f"{emoji} RESULTADO M1\n\n"
                    f"Par: {PAR}\n"
                    f"Direção: "
                    f"{sinal['direcao']}\n"
                    f"Entrada: {entrada}\n"
                    f"Expiração: {saida}\n"
                    f"Resultado: {resultado}"
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

def calcular_resumo(
    sinais
):

    wins = 0
    losses = 0
    empates = 0

    atual_loss = 0
    maior_loss = 0

    sinais_ordenados = sorted(
        sinais.values(),
        key=lambda x:
        x.get(
            "datetime_sinal",
            ""
        )
    )

    for sinal in sinais_ordenados:

        res = str(
            sinal.get(
                "resultado",
                ""
            )
        ).upper()

        if res == "WIN":

            wins += 1

            atual_loss = 0

        elif res == "LOSS":

            losses += 1

            atual_loss += 1

            maior_loss = max(
                maior_loss,
                atual_loss
            )

        elif res == "EMPATE":

            empates += 1

    total = (
        wins
        +
        losses
        +
        empates
    )

    assertividade = (

        wins
        /
        (wins + losses)
        *
        100

    ) if (
        wins + losses
    ) > 0 else 0.0

    saldo = (
        wins - losses
    )

    return {

        "total":
            total,

        "wins":
            wins,

        "losses":
            losses,

        "empates":
            empates,

        "assertividade":
            assertividade,

        "saldo":
            saldo,

        "maior_loss":
            maior_loss,

        "atual_loss":
            atual_loss
    }


def atualizar_resumo(
    aba_resumo,
    sinais
):

    resumo = calcular_resumo(
        sinais
    )

    linha = [

        "Price Action + Exaustao",

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

            range_name="A1:I2",

            values=[
                HEADER_RESUMO,
                linha
            ],

            value_input_option=
            "USER_ENTERED"
        )

    except Exception as e:

        log.warning(
            f"Erro atualizando resumo: {e}"
        )

    return resumo


# ================================================================
# MAIN
# ================================================================

def main():

    threading.Thread(
        target=iniciar_servidor_http,
        daemon=True
    ).start()

    validar_configuracao()

    spreadsheet = (
        conectar_google()
    )

    aba_coletas = (
        obter_aba(
            spreadsheet,
            ABA_COLETAS
        )
    )

    aba_sinais = (
        obter_aba(
            spreadsheet,
            ABA_SINAIS
        )
    )

    aba_resumo = (
        obter_aba(
            spreadsheet,
            ABA_RESUMO
        )
    )

    garantir_cabecalho(
        aba_coletas,
        HEADER_COLETAS
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

    sinais = (
        carregar_sinais(
            aba_sinais
        )
    )

    api = conectar_iq()

    historico = (
        obter_historico_inicial(
            api
        )
    )

    iniciar_stream(
        api
    )

    telegram_enviar(
        "🤖 IQ OPTION BOT V1.3\n\n"
        "Modo: PAPER TRADING\n"
        f"Par: {PAR}\n"
        "Timeframe: M1\n"
        "Expiração: 1 minuto\n"
        "Direções: COMPRA / VENDA\n"
        "Execução real: DESATIVADA"
    )

    log.info(
        "=" * 60
    )

    log.info(
        "IQ OPTION BOT V1.3 ONLINE"
    )

    log.info(
        f"PAR: {PAR}"
    )

    log.info(
        "TIMEFRAME: M1"
    )

    log.info(
        "EXPIRAÇÃO: 1 MINUTO"
    )

    log.info(
        "MODO: PAPER TRADING"
    )

    log.info(
        "=" * 60
    )

    ultimo_timestamp_processado = None

    # ============================================================
    # LOOP COM SINCRONIZAÇÃO EM TEMPO REAL
    # ============================================================

    while True:

        try:

            # 1. ESPERA SINCRONIZADA MATEMATICAMENTE NA VIRADA DO SEGUNDO :00.050
            smart_sleep_proximo_minuto()

            # 2. CHECAGEM DE CONEXÃO
            if not api.check_connect():

                log.warning(
                    "Conexão perdida. "
                    "Reconectando..."
                )

                api = conectar_iq()

                iniciar_stream(
                    api
                )

                continue

            # 3. OBTENÇÃO CIRÚRGICA DOS CANDLES
            candles_stream = (
                obter_stream_candles(
                    api
                )
            )

            if not candles_stream:
                continue

            timestamp_atual = (
                candles_stream[-1][
                    "timestamp"
                ]
            )

            if (
                ultimo_timestamp_processado
                is None
            ):

                ultimo_timestamp_processado = (
                    timestamp_atual
                )

                log.info(
                    "Stream inicializado | "
                    f"Último candle: "
                    f"{candles_stream[-1]['datetime']}"
                )

                continue

            # 4. EXECUÇÃO NA NOVA VELA
            if (
                timestamp_atual
                >
                ultimo_timestamp_processado
            ):

                mapa = {

                    c["timestamp"]: c

                    for c
                    in candles_stream
                }

                ts_fechada = (
                    timestamp_atual
                    -
                    TIMEFRAME
                )

                vela_fechada = (
                    mapa.get(
                        ts_fechada
                    )
                )

                vela_entrada = (
                    mapa.get(
                        timestamp_atual
                    )
                )

                if vela_fechada:

                    # 1. REGISTRAR CANDLE
                    if (
                        ts_fechada
                        not in
                        timestamps_coletas
                    ):

                        sucesso = (
                            registrar_candle(
                                aba_coletas,
                                vela_fechada
                            )
                        )

                        if sucesso:

                            timestamps_coletas.add(
                                ts_fechada
                            )

                            log.info(
                                "📊 CANDLE "
                                f"REGISTRADO | "
                                f"{vela_fechada['datetime']} | "
                                f"O={vela_fechada['open']} | "
                                f"H={vela_fechada['high']} | "
                                f"L={vela_fechada['low']} | "
                                f"C={vela_fechada['close']}"
                            )

                    # 2. FECHAR OPERAÇÕES EXPIRADAS
                    sinais = (
                        carregar_sinais(
                            aba_sinais
                        )
                    )

                    resultados = (
                        atualizar_resultados(
                            aba_sinais,
                            sinais,
                            vela_fechada
                        )
                    )

                    if resultados > 0:

                        sinais = (
                            carregar_sinais(
                                aba_sinais
                            )
                        )

                        resumo = (
                            atualizar_resumo(
                                aba_resumo,
                                sinais
                            )
                        )

                        log.info(
                            "📊 RESUMO | "
                            f"W={resumo['wins']} "
                            f"L={resumo['losses']} "
                            f"E={resumo['empates']} "
                            f"ACC={resumo['assertividade']:.2f}% "
                            f"SALDO={resumo['saldo']}"
                        )

                    # 3. ATUALIZAR HISTÓRICO
                    historico = [

                        c

                        for c
                        in historico

                        if c["timestamp"]
                        !=
                        vela_fechada[
                            "timestamp"
                        ]
                    ]

                    historico.append(
                        vela_fechada
                    )

                    historico.sort(
                        key=lambda x:
                        x["timestamp"]
                    )

                    historico = (
                        historico[
                            -HISTORICO_CANDLES:
                        ]
                    )

                    # 4. GERAR NOVO SINAL
                    sinal, pavio_ratio = (
                        gerar_sinal_estrategia(
                            historico,
                            timestamp_atual
                        )
                    )

                    # 5. CRIAR OPERAÇÃO SIMULADA
                    if (
                        sinal
                        in (
                            "COMPRA",
                            "VENDA"
                        )
                        and
                        vela_entrada
                    ):

                        criar_sinal_simulado(

                            aba_sinais,

                            vela_fechada,

                            vela_entrada,

                            pavio_ratio,

                            sinal,

                            sinais
                        )

                        sinais = (
                            carregar_sinais(
                                aba_sinais
                            )
                        )

                        atualizar_resumo(
                            aba_resumo,
                            sinais
                        )

                    ultimo_timestamp_processado = (
                        timestamp_atual
                    )

        except Exception as e:

            log.exception(
                f"Erro no loop principal: {e}"
            )

            time.sleep(5)


# ================================================================
# EXECUÇÃO
# ================================================================

if __name__ == "__main__":

    main()
