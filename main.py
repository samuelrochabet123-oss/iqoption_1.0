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
