"""Treino de xadrez local: Stockfish + treinador Gemini via CLI `agy`."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
from pathlib import Path
from typing import Any, AsyncIterator, Literal

import chess
import chess.engine
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from storage import Storage

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
load_dotenv(BASE_DIR / ".env")
logger = logging.getLogger("uvicorn.error")

MATE_SCORE = 100_000
MAX_CP_LOSS = 2_000
ANALYSIS_DEPTH = 12
ANALYSIS_TIME_SECONDS = 2.0
FINAL_EVAL_DEPTH = 10
FINAL_EVAL_TIME_SECONDS = 1.0
BOT_TIME_SECONDS = 2.0

# Nível -> (profundidade do bot, Skill Level do Stockfish).
BOT_LEVELS: dict[int, tuple[int, int]] = {
    1: (4, 0),
    2: (6, 4),
    3: (8, 8),
    4: (10, 13),
    5: (12, 20),
}

VALID_AGY_EFFORTS = {"low", "medium", "high", "xhigh", "max"}
VALID_AGY_MODELS = {
    "gemini-4-hydrogen-low",
    "gemini-4-hydrogen-medium",
    "gemini-4-hydrogen-high",
    "gemini-4-hydrogen-xhigh",
}

def _env_number(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


AGY_MODEL = os.getenv("AGY_MODEL", "gemini-4-hydrogen-high").strip()
AGY_EFFORT = os.getenv("AGY_EFFORT", "high").strip().lower()
AGY_TIMEOUT_SECONDS = max(1.0, _env_number("AGY_TIMEOUT_SECONDS", 120.0))
AGY_MAX_CONCURRENCY = max(1, int(_env_number("AGY_MAX_CONCURRENCY", 1)))
AGY_SANDBOX = os.getenv("AGY_SANDBOX", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}

_database_path = Path(os.getenv("DATABASE_PATH", "xadrez.db")).expanduser()
if not _database_path.is_absolute():
    _database_path = BASE_DIR / _database_path
storage = Storage(_database_path)

TRAINER_RULES = (
    "Não use ferramentas, não leia arquivos e não execute comandos: "
    "responda somente com texto, em no máximo 2 parágrafos. "
    "Não use listas nem markdown (nada de asteriscos, crases ou cerquilhas)."
)

TRAINER_BOARD_MARKUP = (
    "MARCAÇÃO PARA DESTACAR NO TABULEIRO:",
    "A interface destaca casas e lances no tabuleiro quando o aluno passa o mouse sobre a resposta.",
    "Para citar uma casa, envolva-a em colchetes duplos: [[e4]].",
    "Para indicar um deslocamento ou lance com direção, use origem e destino separados por >: [[e2>e4]]. Exemplos: [[g1>f3]], [[e4>d5]], [[e1>g1]].",
    "Para roque, use [[O-O]] (pequeno) ou [[O-O-O]] (grande), sempre do lado que está jogando na posição atual; para falar do outro lado ou de uma linha futura, use as casas do rei e da torre, por exemplo [[e8>g8]].",
    "Marcação só para casas e lances reais que você mencionou; não invente, não repita a mesma casa sem necessidade e não use SAN dentro da marcação.",
    "As coordenadas são absolutas (a1 a h8): não diga esquerda ou direita para indicar uma casa, use sempre a coordenada.",
    "A explicação em palavras continua obrigatória; a marcação serve apenas para o destaque visual.",
)

TRAINER_CASTLING_GUIDANCE = (
    "ROQUE E LEGALIDADE:",
    "Use o resumo de roque da posição atual. Nunca mande o aluno tentar O-O ou O-O-O se o resumo disser que não é legal agora.",
    "Se o roque não for legal, explique em palavras o que falta: peça bloqueando a casa entre rei e torre, rei em xeque, casa atacada ou direito de roque já perdido.",
    "Para executar o roque na interface, o aluno arrasta o rei duas casas na direção da torre; a torre se move junto. Não é preciso mover a torre primeiro.",
    "Se o roque aparecer apenas numa linha futura da lousa, deixe claro que antes é preciso mover as peças bloqueadoras.",
)

REVIEW_STUDENT_GUIDANCE: dict[str, tuple[str, ...]] = {
    "beginner": (
        "PERFIL DO ALUNO: INICIANTE.",
        "Explique em linguagem simples, sem presumir que ele domina notação.",
        "Sempre traduza códigos como Nf3, Bf4 ou exd5 para o nome da peça, a casa de origem, a casa de destino e a ideia.",
        "A interface do revisor mostra a lista de lances com classificação e avaliação. Use essa lista como referência; não peça para abrir a lousa do treinador, que fica na outra aba.",
        "Não abra o parecer com centipawns; comece pela ideia e use os números para sustentar o diagnóstico.",
    ),
    "intermediate": (
        "PERFIL DO ALUNO: INTERMEDIÁRIO.",
        "Pode usar notação com moderação, explicando a peça e a ideia de cada lance citado.",
        "A interface do revisor mostra a lista de lances com classificação e avaliação; use essa lista como referência.",
        "Não invente lances nem variantes.",
    ),
    "advanced": (
        "PERFIL DO ALUNO: AVANÇADO.",
        "Pode ser técnico e direto, citando notação, mas explique o raciocínio por trás dos números.",
        "A interface do revisor mostra a lista de lances com classificação e avaliação; use-a como referência.",
        "Não invente lances nem variantes.",
    ),
}

STUDENT_GUIDANCE: dict[str, tuple[str, ...]] = {
    "beginner": (
        "PERFIL DO ALUNO: INICIANTE, ainda aprendendo os fundamentos do xadrez.",
        "Explique como para alguém que não domina notação: diga o nome da peça, de onde ela sai, para onde vai e o que muda na posição (captura, defesa, ataque, desenvolvimento, controle do centro, segurança do rei).",
        "Nunca responda apenas com códigos como Nf3, Bf4, exd5 ou coordenadas soltas. Se precisar citar a notação, explique primeiro em palavras e coloque o código entre parênteses.",
        "A interface tem uma 'lousa do treinador': um tabuleiro virtual com as abas 'Sua jogada' e 'Melhor linha'. Ao explicar uma sequência, peça ao aluno para acompanhar a lousa e descreva os primeiros lances em palavras simples.",
        "Não abra a resposta com a avaliação em centipawns nem recite a linha inteira. Comente no máximo dois ou três lances da sequência no texto e deixe o restante para a lousa.",
        "Deixe claro que a linha da lousa é uma continuação possível calculada pelo motor, não algo que já aconteceu na partida.",
        "Escolha no máximo duas ideias principais e use frases curtas, sem jargão não explicado.",
    ),
    "intermediate": (
        "PERFIL DO ALUNO: INTERMEDIÁRIO. Ele conhece as regras e os temas táticos básicos.",
        "Pode usar notação com moderação, mas sempre associe o código ao nome da peça, à casa de origem e à ideia por trás do lance.",
        "A interface tem uma 'lousa do treinador' com as abas 'Sua jogada' e 'Melhor linha'; use-a como referência para explicar a sequência.",
        "Não invente lances além dos dados fornecidos e explique o conceito antes de aprofundar detalhes.",
    ),
    "advanced": (
        "PERFIL DO ALUNO: AVANÇADO. Ele domina notação e conceitos estratégicos.",
        "Pode ser direto e técnico, citando notação, mas ainda explique o raciocínio tático ou estratégico por trás dos números.",
        "A interface tem uma 'lousa do treinador' com as abas 'Sua jogada' e 'Melhor linha'; refira-se a ela ao comentar a sequência.",
        "Não invente lances além dos dados fornecidos.",
    ),
}


class MoveValidationError(ValueError):
    """O lance ou o FEN enviado pelo cliente não é válido."""


class StockfishUnavailable(RuntimeError):
    """O executável do Stockfish não foi encontrado."""


class StockfishFailure(RuntimeError):
    """O Stockfish falhou durante a análise."""


class ClientDisconnected(Exception):
    """O navegador encerrou a conexão SSE."""


class MoveAndAnalyzeRequest(BaseModel):
    fen: str = Field(..., min_length=10, max_length=120)
    move: str = Field(..., min_length=4, max_length=5)
    level: int = Field(3, ge=1, le=5)
    game_id: int | None = Field(None, ge=1)

    @field_validator("fen")
    @classmethod
    def validate_fen(cls, value: str) -> str:
        try:
            board = chess.Board(value.strip())
        except ValueError as exc:
            raise ValueError("FEN inválido.") from exc
        if not board.is_valid():
            raise ValueError("FEN inválido ou posição ilegal.")
        return board.fen()

    @field_validator("move")
    @classmethod
    def normalize_move(cls, value: str) -> str:
        return value.strip().lower()


class ChatAnalysis(BaseModel):
    fen_before: str | None = Field(None, max_length=120)
    played: str | None = Field(None, max_length=30)
    played_uci: str | None = Field(None, max_length=10)
    best_move: str | None = Field(None, max_length=30)
    best_move_uci: str | None = Field(None, max_length=10)
    pv: list[str] | None = Field(None, max_length=20)
    refutation_pv: list[str] | None = Field(None, max_length=20)
    cp_loss: int | None = Field(None, ge=0, le=MAX_CP_LOSS)
    classification: str | None = Field(None, max_length=30)
    eval_before: str | None = Field(None, max_length=30)
    eval_after: str | None = Field(None, max_length=30)
    bot_move: str | None = Field(None, max_length=30)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4_000)
    fen: str | None = Field(None, max_length=120)
    pgn: str | None = Field(None, max_length=20_000)
    student_level: Literal["beginner", "intermediate", "advanced"] = "beginner"
    analysis: ChatAnalysis | None = None

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("A mensagem não pode ficar vazia.")
        return clean


def _validated_fen(value: str) -> str:
    try:
        board = chess.Board(value.strip())
    except ValueError as exc:
        raise ValueError("FEN inválido.") from exc
    if not board.is_valid():
        raise ValueError("FEN inválido ou posição ilegal.")
    return board.fen()


class CreateGameRequest(BaseModel):
    level: int = Field(3, ge=1, le=5)
    student_level: Literal["beginner", "intermediate", "advanced"] = "beginner"
    start_fen: str = Field(chess.STARTING_FEN, min_length=10, max_length=120)

    @field_validator("start_fen")
    @classmethod
    def validate_start_fen(cls, value: str) -> str:
        return _validated_fen(value)


class EndGameRequest(BaseModel):
    reason: str = Field("finished", max_length=80)
    result: str | None = Field(None, max_length=16)


class CurrentGameRequest(BaseModel):
    game_id: int = Field(..., ge=1)


class ResignRequest(CreateGameRequest):
    game_id: int | None = Field(None, ge=1)


class UndoRequest(BaseModel):
    plies: int = Field(2, ge=1, le=4)
    fen: str = Field(..., min_length=10, max_length=120)
    pgn: str | None = Field(None, max_length=20_000)
    reason: str = Field("undo", max_length=80)

    @field_validator("fen")
    @classmethod
    def validate_fen(cls, value: str) -> str:
        return _validated_fen(value)


class EventRequest(BaseModel):
    type: str = Field(..., min_length=1, max_length=50)
    payload: dict[str, Any] | None = None


class ReviewFeedbackRequest(BaseModel):
    student_level: Literal["beginner", "intermediate", "advanced"] = "beginner"


class ReviewGameFeedbackRequest(ReviewFeedbackRequest):
    pass


class ChatLimiter:
    """Admite uma fila curta e limita chamadas simultâneas ao CLI."""

    def __init__(self, concurrency: int, queue_size: int) -> None:
        self._semaphore = asyncio.Semaphore(concurrency)
        self._capacity = concurrency + queue_size
        self._admitted = 0

    def reserve(self) -> bool:
        if self._admitted >= self._capacity:
            return False
        self._admitted += 1
        return True

    def unreserve(self) -> None:
        self._admitted = max(0, self._admitted - 1)

    async def acquire(self) -> None:
        await self._semaphore.acquire()

    def release(self) -> None:
        self._semaphore.release()


chat_limiter = ChatLimiter(AGY_MAX_CONCURRENCY, max(1, AGY_MAX_CONCURRENCY * 2))

app = FastAPI(
    title="Treino de Xadrez Local",
    description="Stockfish local + treinador Gemini via CLI agy.",
    version="1.0.0",
)

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _is_executable(path: str) -> bool:
    return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)


def resolve_stockfish_path() -> str | None:
    """Resolve STOCKFISH_PATH e os fallbacks documentados."""
    candidates: list[str] = []
    configured = os.getenv("STOCKFISH_PATH", "").strip()
    if configured:
        candidates.append(configured)
    candidates.extend(
        [
            "/usr/bin/stockfish",
            "~/.local/bin/stockfish",
            "/usr/local/bin/stockfish",
            "stockfish",
        ]
    )

    seen: set[str] = set()
    for candidate in candidates:
        expanded = os.path.expanduser(candidate)
        if expanded in seen:
            continue
        seen.add(expanded)
        resolved = shutil.which(expanded)
        if resolved and _is_executable(resolved):
            return str(Path(resolved).resolve())
    return None


def resolve_agy_path() -> str:
    """Retorna o caminho configurado do agy, exista ele ou não."""
    configured = os.getenv("AGY_BIN", "~/.local/bin/agy").strip() or "~/.local/bin/agy"
    expanded = os.path.expanduser(configured)
    resolved = shutil.which(expanded)
    if resolved and _is_executable(resolved):
        return str(Path(resolved).resolve())
    return str(Path(expanded).absolute())


def _open_engine(path: str) -> chess.engine.SimpleEngine:
    try:
        engine = chess.engine.SimpleEngine.popen_uci(path, timeout=15)
    except FileNotFoundError as exc:
        raise StockfishUnavailable(
            "Stockfish não encontrado. Instale o Stockfish e configure STOCKFISH_PATH no .env."
        ) from exc
    except (chess.engine.EngineError, OSError) as exc:
        raise StockfishFailure(f"Não foi possível iniciar o Stockfish: {exc}") from exc

    for name, value in (("Threads", 1), ("Hash", 16), ("Skill Level", 20)):
        try:
            engine.configure({name: value})
        except chess.engine.EngineError:
            # Algumas builds não expõem todas as opções UCI.
            continue
    return engine


def _set_skill(engine: chess.engine.SimpleEngine, skill: int) -> None:
    try:
        engine.configure({"Skill Level": skill})
    except chess.engine.EngineError:
        pass


def _analyse(
    engine: chess.engine.SimpleEngine,
    board: chess.Board,
    depth: int,
    seconds: float,
) -> tuple[chess.engine.PovScore, list[chess.Move]]:
    try:
        info = engine.analyse(
            board,
            chess.engine.Limit(depth=depth, time=seconds),
        )
    except (chess.engine.EngineError, chess.engine.EngineTerminatedError) as exc:
        raise StockfishFailure(f"Falha na análise do Stockfish: {exc}") from exc

    score = info.get("score")
    if score is None:
        raise StockfishFailure("O Stockfish não retornou avaliação para a posição.")
    pv = info.get("pv") or []
    return score, pv


def _score_cp(score: chess.engine.PovScore, pov: chess.Color) -> int:
    return score.pov(pov).score(mate_score=MATE_SCORE)


def _eval_from_score(score: chess.engine.PovScore) -> dict[str, Any]:
    white = score.pov(chess.WHITE)
    if white.is_mate():
        mate = white.mate()
        return {
            "type": "mate",
            "value": mate,
            "label": f"#{mate}",
            "white_cp": 1000 if mate > 0 else -1000,
        }

    cp = white.score()
    label = "0.00" if cp == 0 else f"{cp / 100:+.2f}"
    return {
        "type": "cp",
        "value": cp,
        "label": label,
        "white_cp": max(-1000, min(1000, cp)),
    }


def _terminal_cp(board: chess.Board, pov: chess.Color) -> int:
    if board.is_checkmate():
        return -MATE_SCORE if board.turn == pov else MATE_SCORE
    return 0


def _terminal_eval(board: chess.Board) -> dict[str, Any]:
    if board.is_checkmate():
        white_wins = board.turn == chess.BLACK
        return {
            "type": "mate",
            "value": 0,
            "label": "#",
            "white_cp": 1000 if white_wins else -1000,
            "terminal": "checkmate",
            "winner": "white" if white_wins else "black",
        }
    return {
        "type": "cp",
        "value": 0,
        "label": "½–½",
        "white_cp": 0,
        "terminal": "draw",
    }


def _pv_to_san(board: chess.Board, pv: list[chess.Move], limit: int = 10) -> list[str]:
    copy = board.copy(stack=False)
    result: list[str] = []
    for move in pv[:limit]:
        if move not in copy.legal_moves:
            break
        result.append(copy.san(move))
        copy.push(move)
    return result


def _classify(
    played: chess.Move,
    best: chess.Move | None,
    cp_loss: int,
    board_after_user: chess.Board,
    user_color: chess.Color,
) -> str:
    if board_after_user.is_checkmate() and board_after_user.turn != user_color:
        return "Excelente"
    if best is not None and played == best:
        return "Excelente"
    if cp_loss <= 20:
        return "Excelente"
    if cp_loss <= 50:
        return "Bom"
    if cp_loss <= 100:
        return "Imprecisão"
    if cp_loss <= 200:
        return "Erro"
    return "Gafe"


def _game_result(board: chess.Board) -> str | None:
    outcome = board.outcome()
    return outcome.result() if outcome is not None else None


def _move_and_analyze_sync(
    fen: str,
    move_uci: str,
    level: int,
) -> dict[str, Any]:
    try:
        board_before = chess.Board(fen)
    except ValueError as exc:
        raise MoveValidationError("FEN inválido.") from exc

    try:
        move = chess.Move.from_uci(move_uci)
    except ValueError as exc:
        raise MoveValidationError("Lance UCI inválido.") from exc
    if move not in board_before.legal_moves:
        raise MoveValidationError("Esse lance não é legal na posição informada.")

    user_color = board_before.turn
    played_san = board_before.san(move)
    stockfish_path = resolve_stockfish_path()
    if not stockfish_path:
        raise StockfishUnavailable(
            "Stockfish não encontrado. Instale o Stockfish e configure "
            "STOCKFISH_PATH no .env (veja o README)."
        )

    engine = _open_engine(stockfish_path)
    try:
        score_before, pv_before = _analyse(
            engine, board_before, ANALYSIS_DEPTH, ANALYSIS_TIME_SECONDS
        )
        best_move = pv_before[0] if pv_before else None
        best_san = board_before.san(best_move) if best_move is not None else None
        pv_san = _pv_to_san(board_before, pv_before)
        eval_before = _eval_from_score(score_before)
        cp_before_user = _score_cp(score_before, user_color)

        board_after_user = board_before.copy(stack=False)
        board_after_user.push(move)

        after_best: chess.Move | None = None
        refutation_pv: list[chess.Move] = []
        if board_after_user.is_game_over():
            cp_after_user = _terminal_cp(board_after_user, user_color)
            eval_after_user = _terminal_eval(board_after_user)
        else:
            score_after, pv_after = _analyse(
                engine, board_after_user, ANALYSIS_DEPTH, ANALYSIS_TIME_SECONDS
            )
            after_best = pv_after[0] if pv_after else None
            refutation_pv = pv_after
            cp_after_user = _score_cp(score_after, user_color)
            eval_after_user = _eval_from_score(score_after)

        refutation_pv_san = _pv_to_san(board_after_user, refutation_pv)

        cp_loss = max(0, min(MAX_CP_LOSS, cp_before_user - cp_after_user))
        classification = _classify(
            move, best_move, cp_loss, board_after_user, user_color
        )

        bot_depth, bot_skill = BOT_LEVELS[level]
        board_after_bot = board_after_user.copy(stack=False)
        bot_move_data: dict[str, str] | None = None
        eval_after_bot = eval_after_user

        if not board_after_user.is_game_over():
            bot_move: chess.Move | None = None
            if level == 5 and after_best is not None:
                bot_move = after_best
            else:
                _set_skill(engine, bot_skill)
                try:
                    _, bot_pv = _analyse(
                        engine, board_after_user, bot_depth, BOT_TIME_SECONDS
                    )
                finally:
                    _set_skill(engine, 20)
                if bot_pv:
                    bot_move = bot_pv[0]

            if bot_move is None or bot_move not in board_after_user.legal_moves:
                if after_best is not None and after_best in board_after_user.legal_moves:
                    bot_move = after_best
                else:
                    bot_move = next(iter(board_after_user.legal_moves), None)

            if bot_move is not None:
                bot_san = board_after_user.san(bot_move)
                board_after_bot.push(bot_move)
                bot_move_data = {"uci": bot_move.uci(), "san": bot_san}

                if board_after_bot.is_game_over():
                    eval_after_bot = _terminal_eval(board_after_bot)
                else:
                    score_final, _ = _analyse(
                        engine,
                        board_after_bot,
                        FINAL_EVAL_DEPTH,
                        FINAL_EVAL_TIME_SECONDS,
                    )
                    eval_after_bot = _eval_from_score(score_final)

        return {
            "fen_before": board_before.fen(),
            "fen_after_user": board_after_user.fen(),
            "fen_after_bot": board_after_bot.fen(),
            "user_move": {"uci": move.uci(), "san": played_san},
            "best_move": {
                "uci": best_move.uci() if best_move is not None else None,
                "san": best_san,
            },
            "bot_move": bot_move_data,
            "pv": [m.uci() for m in pv_before[:10]],
            "pv_san": pv_san,
            "refutation_pv": [m.uci() for m in refutation_pv[:10]],
            "refutation_pv_san": refutation_pv_san,
            "evaluation": {
                "before": eval_before,
                "after_user": eval_after_user,
                "after_bot": eval_after_bot,
            },
            "cp_loss": cp_loss,
            "classification": classification,
            "level": level,
            "analysis_depth": ANALYSIS_DEPTH,
            "bot_depth": bot_depth,
            "turn": "white" if board_after_bot.turn == chess.WHITE else "black",
            "game_over": board_after_bot.is_game_over(),
            "result": _game_result(board_after_bot),
        }
    finally:
        try:
            engine.quit()
        except Exception:
            engine.close()


def _display_fen(fen: str | None) -> str:
    if not fen:
        return "não informado"
    try:
        board = chess.Board(fen)
    except ValueError:
        return "FEN inválido"
    return board.fen() if board.is_valid() else "FEN inválido"


def _display_pgn(pgn: str | None) -> str:
    if not pgn:
        return "(sem lances)"
    clean = pgn.replace("\x00", "").strip()
    if len(clean) > 8_000:
        return "…(início omitido)…\n" + clean[-8_000:]
    return clean


def _castling_summary(fen: str | None) -> str:
    if not fen:
        return "não informado"
    try:
        board = chess.Board(fen)
    except ValueError:
        return "FEN inválido"
    if not board.is_valid():
        return "posição inválida"

    white = board.turn == chess.WHITE
    king_home = chess.E1 if white else chess.E8
    kingside_target = chess.G1 if white else chess.G8
    queenside_target = chess.C1 if white else chess.C8

    legal_moves = list(board.legal_moves)
    kingside = any(
        move.from_square == king_home and move.to_square == kingside_target
        for move in legal_moves
    )
    queenside = any(
        move.from_square == king_home and move.to_square == queenside_target
        for move in legal_moves
    )

    rights: list[str] = []
    for color, label in ((chess.WHITE, "brancas"), (chess.BLACK, "pretas")):
        sides: list[str] = []
        if board.has_kingside_castling_rights(color):
            sides.append("pequeno")
        if board.has_queenside_castling_rights(color):
            sides.append("grande")
        rights.append(f"{label}: {', '.join(sides) if sides else 'nenhum'}")

    side = "brancas" if white else "pretas"
    return (
        f"lado a jogar: {side}; "
        f"roque legal agora: pequeno {'sim' if kingside else 'não'}, "
        f"grande {'sim' if queenside else 'não'}; "
        f"direitos restantes: {'; '.join(rights)}"
    )


def build_trainer_prompt(request: ChatRequest) -> str:
    lines = [
        "Você é um treinador de xadrez direto, didático e conciso, falando em português do Brasil.",
        "Sua tarefa é explicar ao aluno o conceito tático ou estratégico por trás dos números do Stockfish.",
        "Nunca invente lances, variantes, avaliações ou peças que não estejam nos dados abaixo.",
        "Se os dados do motor não estiverem disponíveis, diga isso com franqueza e não invente um melhor lance.",
        "Responda em no máximo 2 parágrafos, sem listas longas e sem markdown complicado.",
        "",
        *STUDENT_GUIDANCE[request.student_level],
        "",
        *TRAINER_BOARD_MARKUP,
        "",
        *TRAINER_CASTLING_GUIDANCE,
        "",
        "DADOS DA PARTIDA",
        f"FEN atual: {_display_fen(request.fen)}",
        f"Roque na posição atual: {_castling_summary(request.fen)}",
        "Histórico PGN:",
        _display_pgn(request.pgn),
    ]

    analysis = request.analysis
    if analysis is not None:
        lines.extend(["", "ANÁLISE DO STOCKFISH PARA O LANCE DO ALUNO"])
        if analysis.fen_before:
            lines.append(f"FEN antes do lance: {_display_fen(analysis.fen_before)}")
            lines.append(
                f"Roque antes do lance: {_castling_summary(analysis.fen_before)}"
            )
        if analysis.played:
            uci = f" ({analysis.played_uci})" if analysis.played_uci else ""
            lines.append(f"Lance jogado pelo aluno: {analysis.played}{uci}")
        if analysis.best_move:
            uci = f" ({analysis.best_move_uci})" if analysis.best_move_uci else ""
            lines.append(f"Melhor lance segundo o Stockfish: {analysis.best_move}{uci}")
        if analysis.pv:
            lines.append(f"Linha principal (PV): {' '.join(analysis.pv)}")
        if analysis.refutation_pv:
            lines.append(
                "Linha de refutação, depois do lance do aluno: "
                + " ".join(analysis.refutation_pv)
            )
        if analysis.cp_loss is not None:
            lines.append(f"Perda de avaliação: {analysis.cp_loss} centipawns")
        if analysis.classification:
            lines.append(f"Classificação: {analysis.classification}")
        if analysis.eval_before:
            lines.append(f"Avaliação antes: {analysis.eval_before}")
        if analysis.eval_after:
            lines.append(f"Avaliação depois: {analysis.eval_after}")
        if analysis.bot_move:
            lines.append(f"Resposta jogada pelo bot: {analysis.bot_move}")

    lines.extend(
        [
            "",
            f"Pergunta do aluno: {request.message}",
            "",
            TRAINER_RULES,
        ]
    )
    return "\n".join(lines)


REVIEW_RULES = (
    "Não use ferramentas, não leia arquivos e não execute comandos: "
    "responda somente com texto, em no máximo 4 parágrafos. "
    "Não use markdown nem crases."
)


def _classification_line(counts: dict[str, int]) -> str:
    order = ("Excelente", "Bom", "Imprecisão", "Erro", "Gafe")
    return ", ".join(f"{label}: {counts.get(label, 0)}" for label in order)


def _format_review_stats(stats: dict[str, Any]) -> list[str]:
    lines = [
        f"Partidas com lances: {stats.get('games', 0)}",
        f"Lances do aluno analisados: {stats.get('analyzed_user_moves', 0)}",
        f"Classificações: {_classification_line(stats.get('classifications') or {})}",
        f"Perda média: {stats.get('avg_cp_loss')} centipawns",
        f"Precisão estimada: {stats.get('accuracy')}%",
        f"Taxa de lances ruins (imprecisão/erro/gafe): {stats.get('bad_rate')}%",
    ]

    phases = stats.get("phases") or {}
    lines.append("Por fase:")
    for phase in ("abertura", "meio-jogo", "final"):
        item = phases.get(phase) or {}
        lines.append(
            f"- {phase}: {item.get('bad', 0)} ruins de {item.get('total', 0)} "
            f"({item.get('bad_rate')}%)"
        )

    pieces = stats.get("pieces") or {}
    if pieces:
        lines.append("Por peça movida:")
        for piece, item in sorted(
            pieces.items(), key=lambda pair: (-pair[1].get("bad", 0), pair[0])
        )[:8]:
            lines.append(
                f"- {piece}: {item.get('bad', 0)} ruins de {item.get('total', 0)} "
                f"({item.get('bad_rate')}%)"
            )

    openings = stats.get("openings") or []
    if openings:
        lines.append("Sequências iniciais mais repetidas:")
        for item in openings[:5]:
            lines.append(f"- {item.get('line')}: {item.get('count')} vez(es)")

    trend = stats.get("trend") or {}
    lines.append(
        "Tendência: "
        f"precisão recente {trend.get('recent_accuracy')}%, "
        f"precisão anterior {trend.get('previous_accuracy')}%; "
        f"perda média recente {trend.get('recent_avg_cp_loss')} cp, "
        f"perda anterior {trend.get('previous_avg_cp_loss')} cp."
    )

    recent_games = stats.get("recent_games") or []
    if recent_games:
        lines.append("Últimas partidas:")
        for game in recent_games[:8]:
            lines.append(
                f"- #{game.get('id')} em {str(game.get('started_at', ''))[:10]}: "
                f"resultado {game.get('result') or 'em andamento'}, "
                f"nível {game.get('level')}, "
                f"{game.get('analyzed_user_moves', 0)} lances analisados, "
                f"perda média {game.get('avg_cp_loss')} cp, "
                f"precisão {game.get('accuracy')}%, "
                f"classificações ({_classification_line(game.get('classifications') or {})})."
            )

    return lines


def build_review_prompt(stats: dict[str, Any], student_level: str) -> str:
    lines = [
        "Você é um revisor de xadrez que acompanha a evolução de um aluno ao longo de várias partidas.",
        "Seu papel é apontar progresso real, padrões de erro, conceitos que o aluno está esquecendo e próximos passos práticos.",
        "Use apenas os dados objetivos abaixo. Não invente lances, números, partidas ou padrões que não estejam nos dados.",
        "Se os dados forem insuficientes, diga isso claramente e peça mais partidas em vez de inventar conclusões.",
        "",
        *REVIEW_STUDENT_GUIDANCE[student_level],
        "",
        "DADOS AGREGADOS DO HISTÓRICO",
        *_format_review_stats(stats),
        "",
        "TAREFA DE REVISÃO",
        "Escreva um parecer de progresso em português do Brasil, com no máximo 4 parágrafos.",
        "Diga o que melhorou, o que piorou ou continua repetindo, em qual fase/peça/abertura o aluno mais perde pontos e o que ele parece estar esquecendo.",
        "Termine com 2 ou 3 recomendações concretas para as próximas partidas. Não tente ensinar uma abertura inteira nem inventar variantes.",
        "",
        REVIEW_RULES,
    ]
    return "\n".join(lines)


def _format_game_moves(game_data: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for move in game_data.get("moves", []):
        actor = "aluno" if move.get("actor") == "user" else "Stockfish"
        number = f"{move.get('move_number')}{'.' if move.get('color') == 'w' else '...'}"
        details = [f"{number} {move.get('san')}", f"[{actor}]"]
        if move.get("classification"):
            details.append(f"classificação {move['classification']}")
        if move.get("cp_loss") is not None and move.get("actor") == "user":
            details.append(f"perda {move['cp_loss']} cp")
        if move.get("eval_before") is not None or move.get("eval_after") is not None:
            details.append(
                f"avaliação {move.get('eval_before')} → {move.get('eval_after')}"
            )
        if move.get("actor") == "user" and move.get("best_move_san"):
            details.append(f"melhor era {move['best_move_san']}")
        lines.append(" | ".join(details))
        refutation = move.get("refutation_pv") or []
        if move.get("actor") == "user" and refutation:
            lines.append(
                "  continuação calculada depois desse lance "
                "(o adversário responde primeiro): "
                + " ".join(refutation[:6])
            )

    undone = game_data.get("undone_moves") or []
    if undone:
        lines.append("")
        lines.append("Lances desfeitos pelo aluno (não valem para a partida atual):")
        for move in undone[:30]:
            lines.append(
                f"- {move.get('san')} [{move.get('actor')}] "
                f"{move.get('classification') or 'sem classificação'}"
            )

    events = game_data.get("events") or []
    action_types = [
        event.get("type")
        for event in events
        if event.get("type") in {"undo", "game_ended", "game_over", "game_started"}
    ]
    if action_types:
        lines.append("")
        lines.append("Ações registradas: " + ", ".join(action_types[:20]))
    return lines


def build_game_review_prompt(
    game_data: dict[str, Any],
    student_level: str,
) -> str:
    game = game_data.get("game") or {}
    summary = game_data.get("summary") or {}
    lines = [
        "Você é um revisor de xadrez analisando uma partida inteira de um aluno.",
        "Use exclusivamente os lances, classificações, avaliações e números abaixo. Não invente lances, variantes nem avaliações.",
        "Considere tanto os lances ativos quanto a lista de lances desfeitos para entender o comportamento do aluno.",
        "",
        *REVIEW_STUDENT_GUIDANCE[student_level],
        "",
        "DADOS DA PARTIDA",
        f"Partida #{game.get('id')} iniciada em {game.get('started_at')}",
        f"Nível do bot: {game.get('level')}",
        f"Resultado: {game.get('result') or 'em andamento'}",
        f"Classificações do aluno: {_classification_line(summary.get('classifications') or {})}",
        f"Perda média: {summary.get('avg_cp_loss')} centipawns",
        f"Precisão estimada: {summary.get('accuracy')}%",
        f"Lances ativos: {summary.get('plies', 0)}; lances desfeitos: {summary.get('undone_moves', 0)}",
        "",
        "LANCES DA PARTIDA",
        *_format_game_moves(game_data),
        "",
        "TAREFA DE REVISÃO DA PARTIDA",
        "Escreva uma análise da partida inteira em português do Brasil, com no máximo 4 parágrafos.",
        "Explique os 3 a 5 momentos mais importantes em linguagem simples, diga o que o aluno não viu, relacione os erros com padrões (fase, peça, abertura) e termine com recomendações práticas.",
        "Não recite a lista inteira de lances; use a lousa e os dados como apoio.",
        "",
        REVIEW_RULES,
    ]
    return "\n".join(lines)


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _agy_args(agy_path: str) -> list[str]:
    args = [
        agy_path,
        "--print=",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--model",
        AGY_MODEL,
        "--effort",
        AGY_EFFORT,
        "--disable-slash-commands",
    ]
    if AGY_SANDBOX:
        args.append("--sandbox")
    return args


def _kill_process_group(proc: asyncio.subprocess.Process | None) -> None:
    """SIGKILL síncrono: precisa funcionar mesmo com o gerador cancelado."""
    if proc is None or proc.returncode is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


async def _reap_process(proc: asyncio.subprocess.Process | None) -> None:
    if proc is None:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except (TimeoutError, asyncio.CancelledError):
        pass
    except Exception:
        pass


async def _run_agy(
    prompt: str,
    request: Request,
    agy_path: str,
) -> AsyncIterator[str]:
    proc: asyncio.subprocess.Process | None = None
    pump_task: asyncio.Task[None] | None = None
    stderr_task: asyncio.Task[None] | None = None
    stderr_chunks: list[str] = []
    saw_result = False

    try:
        proc = await asyncio.create_subprocess_exec(
            *_agy_args(agy_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(BASE_DIR),
            env=os.environ.copy(),
            start_new_session=True,
        )

        assert proc.stdin is not None
        payload = json.dumps(
            {
                "event": "user",
                "message": {"role": "user", "content": prompt},
            },
            ensure_ascii=False,
        )
        proc.stdin.write((payload + "\n").encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

        queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def pump_stdout() -> None:
            assert proc is not None and proc.stdout is not None
            try:
                while True:
                    raw = await proc.stdout.readline()
                    if not raw:
                        break
                    await queue.put(raw.decode("utf-8", errors="replace"))
            finally:
                await queue.put(None)

        async def drain_stderr() -> None:
            assert proc is not None and proc.stderr is not None
            while True:
                raw = await proc.stderr.readline()
                if not raw:
                    break
                stderr_chunks.append(raw.decode("utf-8", errors="replace"))
                if len(stderr_chunks) > 200:
                    del stderr_chunks[:100]

        pump_task = asyncio.create_task(pump_stdout())
        stderr_task = asyncio.create_task(drain_stderr())

        loop = asyncio.get_running_loop()
        deadline = loop.time() + AGY_TIMEOUT_SECONDS
        last_heartbeat = loop.time()

        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError

            try:
                line = await asyncio.wait_for(
                    queue.get(), timeout=min(0.5, remaining)
                )
            except TimeoutError:
                if await request.is_disconnected():
                    raise ClientDisconnected
                # Um comentário SSE força a detecção de desconexão pelo
                # servidor e mantém proxies/navegadores cientes de que o
                # stream está vivo enquanto o modelo pensa.
                if loop.time() - last_heartbeat >= 5:
                    last_heartbeat = loop.time()
                    yield ": ping\n\n"
                continue

            if line is None:
                break
            line = line.strip()
            if not line:
                continue

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue

            event_type = event.get("event")
            if event_type == "step_update":
                step = event.get("step_update")
                if not isinstance(step, dict):
                    continue
                if step.get("step_type") != "agent_response":
                    continue
                delta = step.get("text_delta")
                if isinstance(delta, str) and delta:
                    yield _sse({"delta": delta})
            elif event_type == "result":
                result = event.get("result")
                result = result if isinstance(result, dict) else {}
                yield _sse(
                    {
                        "done": True,
                        "status": result.get("status"),
                        "usage": result.get("usage"),
                    }
                )
                saw_result = True
                break
            # Eventos "init" e steps de ferramenta são ignorados de propósito.

        if not saw_result:
            if stderr_task is not None and not stderr_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(stderr_task), timeout=1)
                except Exception:
                    pass

            return_code = proc.returncode
            if return_code is None:
                try:
                    return_code = await asyncio.wait_for(proc.wait(), timeout=3)
                except TimeoutError:
                    return_code = None

            if return_code not in (0, None):
                detail = " ".join(stderr_chunks).strip()[-1_500:]
                message = f"agy terminou com código {return_code}."
                if detail:
                    message += f" Detalhe: {detail}"
                yield _sse(
                    {"error": message, "done": True, "status": "ERROR"}
                )
            else:
                yield _sse(
                    {
                        "error": "agy encerrou sem enviar o evento result.",
                        "done": True,
                        "status": "NO_RESULT",
                    }
                )

    except ClientDisconnected:
        return
    except TimeoutError:
        yield _sse(
            {
                "error": (
                    "O treinador excedeu o limite de "
                    f"{AGY_TIMEOUT_SECONDS:.0f} s e foi interrompido."
                ),
                "done": True,
                "status": "TIMEOUT",
            }
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        yield _sse(
            {
                "error": f"Falha ao executar o agy: {exc}",
                "done": True,
                "status": "ERROR",
            }
        )
    finally:
        # Mata antes de qualquer await: durante a desconexão do cliente o
        # gerador é cancelado e um await pode ser interrompido no meio.
        _kill_process_group(proc)

        if proc is not None and proc.stdin is not None and not proc.stdin.is_closing():
            try:
                proc.stdin.close()
            except Exception:
                pass

        for task in (pump_task, stderr_task):
            if task is not None and not task.done():
                task.cancel()
        try:
            await asyncio.gather(
                *[task for task in (pump_task, stderr_task) if task is not None],
                return_exceptions=True,
            )
        except BaseException:
            pass

        try:
            await asyncio.shield(_reap_process(proc))
        except BaseException:
            pass


async def _stream_chat(
    prompt: str,
    request: Request,
    agy_path: str,
) -> AsyncIterator[str]:
    acquired = False
    try:
        yield ": conectado ao treinador\n\n"
        while True:
            try:
                await asyncio.wait_for(chat_limiter.acquire(), timeout=5)
                acquired = True
                break
            except TimeoutError:
                yield ": aguardando vaga na fila\n\n"
        async for chunk in _run_agy(prompt, request, agy_path):
            yield chunk
    finally:
        if acquired:
            chat_limiter.release()
        chat_limiter.unreserve()


def _probe_stockfish(path: str) -> tuple[bool, str | None]:
    try:
        engine = chess.engine.SimpleEngine.popen_uci(path, timeout=10)
    except Exception:
        return False, None

    name: str | None = None
    try:
        engine.ping()
        engine_id = getattr(engine, "id", None)
        if isinstance(engine_id, dict):
            value = engine_id.get("name")
            name = value if isinstance(value, str) else None
        return True, name
    except Exception:
        return False, name
    finally:
        try:
            engine.quit()
        except Exception:
            engine.close()


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, Any]:
    stockfish_path = resolve_stockfish_path()
    uci_ok = False
    stockfish_name: str | None = None
    if stockfish_path:
        try:
            uci_ok, stockfish_name = await asyncio.wait_for(
                asyncio.to_thread(_probe_stockfish, stockfish_path),
                timeout=12,
            )
        except TimeoutError:
            uci_ok = False

    agy_path = resolve_agy_path()
    agy_found = _is_executable(agy_path)

    warnings: list[str] = []
    if not stockfish_path:
        warnings.append(
            "Stockfish não encontrado. Instale o Stockfish e configure STOCKFISH_PATH."
        )
    elif not uci_ok:
        warnings.append("Stockfish encontrado, mas não respondeu ao comando uci.")
    if not agy_found:
        warnings.append("agy não encontrado neste host; o chat do treinador não funciona.")
    if AGY_EFFORT not in VALID_AGY_EFFORTS:
        warnings.append(f"AGY_EFFORT inválido: {AGY_EFFORT!r}.")
    if AGY_MODEL not in VALID_AGY_MODELS:
        warnings.append(f"AGY_MODEL inválido: {AGY_MODEL!r}.")

    return {
        "status": "ok" if stockfish_path and uci_ok and agy_found else "degraded",
        "stockfish": {
            "path": stockfish_path,
            "found": stockfish_path is not None,
            "uci": uci_ok,
            "name": stockfish_name,
        },
        "agy": {
            "path": agy_path,
            "found": agy_found,
            "model": AGY_MODEL,
            "effort": AGY_EFFORT,
            "sandbox": AGY_SANDBOX,
            "timeout_seconds": AGY_TIMEOUT_SECONDS,
            "max_concurrency": AGY_MAX_CONCURRENCY,
        },
        "database": {
            "path": str(storage.path),
            "exists": storage.path.exists(),
        },
        "warnings": warnings,
    }


async def _streaming_agy_response(
    prompt: str,
    request: Request,
) -> StreamingResponse:
    agy_path = resolve_agy_path()
    if not _is_executable(agy_path):
        raise HTTPException(
            status_code=503,
            detail=(
                "agy não encontrado neste host. O chat do treinador só funciona "
                "onde o CLI agy estiver instalado e autenticado localmente."
            ),
        )
    if AGY_EFFORT not in VALID_AGY_EFFORTS:
        raise HTTPException(
            status_code=500,
            detail=f"AGY_EFFORT inválido: {AGY_EFFORT!r}.",
        )
    if AGY_MODEL not in VALID_AGY_MODELS:
        raise HTTPException(
            status_code=500,
            detail=f"AGY_MODEL inválido: {AGY_MODEL!r}.",
        )

    if not chat_limiter.reserve():
        raise HTTPException(
            status_code=429,
            detail=(
                "O treinador está ocupado com outras análises. "
                "Aguarde alguns segundos e tente novamente."
            ),
        )

    try:
        response = StreamingResponse(
            _stream_chat(prompt, request, agy_path),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
    except Exception:
        chat_limiter.unreserve()
        raise
    return response


@app.get("/session/current")
async def session_current() -> dict[str, Any]:
    current = await asyncio.to_thread(storage.get_current_game)
    return {"current": current}


@app.post("/session/current")
async def session_set_current(body: CurrentGameRequest) -> dict[str, Any]:
    claimed = await asyncio.to_thread(storage.set_current_game, body.game_id)
    if not claimed:
        raise HTTPException(status_code=404, detail="Partida não encontrada.")
    return {"ok": True}


@app.post("/session/resign")
async def session_resign(body: ResignRequest) -> dict[str, Any]:
    game_id = body.game_id
    if game_id is None:
        current = await asyncio.to_thread(storage.get_current_game)
        if current is not None:
            game_id = int(current["game"]["id"])

    ended_game_id: int | None = None
    result: str | None = None

    if game_id is not None:
        game_data = await asyncio.to_thread(storage.get_game, game_id)
        if game_data is not None and not game_data["game"]["ended_at"]:
            user_color = game_data["game"]["user_color"]
            result = "0-1" if user_color == "white" else "1-0"
            ended = await asyncio.to_thread(
                storage.end_game,
                game_id,
                "resign",
                result,
            )
            if ended:
                ended_game_id = game_id
                try:
                    await asyncio.to_thread(
                        storage.record_event,
                        game_id,
                        "resign",
                        {"result": result},
                    )
                except Exception:
                    logger.exception(
                        "Falha ao registrar o evento de desistência da partida %s",
                        game_id,
                    )

    new_game = await asyncio.to_thread(
        storage.create_game,
        body.level,
        body.student_level,
        body.start_fen,
    )
    return {
        "ended_game_id": ended_game_id,
        "result": result,
        "game": new_game,
    }


@app.post("/games")
async def create_game(body: CreateGameRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        storage.create_game,
        body.level,
        body.student_level,
        body.start_fen,
    )


@app.get("/games")
async def list_games(
    limit: int = Query(30, ge=1, le=100),
    include_empty: bool = False,
) -> dict[str, Any]:
    games = await asyncio.to_thread(storage.list_games, limit, include_empty)
    return {"games": games}


@app.get("/games/{game_id}")
async def get_game(game_id: int) -> dict[str, Any]:
    game = await asyncio.to_thread(storage.get_game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="Partida não encontrada.")
    return game


@app.post("/games/{game_id}/end")
async def end_game(game_id: int, body: EndGameRequest) -> dict[str, Any]:
    ended = await asyncio.to_thread(
        storage.end_game,
        game_id,
        body.reason,
        body.result,
    )
    if not ended:
        raise HTTPException(status_code=404, detail="Partida não encontrada.")
    return {"ok": True}


@app.post("/games/{game_id}/undo")
async def undo_game(game_id: int, body: UndoRequest) -> dict[str, Any]:
    return await asyncio.to_thread(
        storage.record_undo,
        game_id,
        body.plies,
        body.fen,
        body.pgn,
        body.reason,
    )


@app.post("/games/{game_id}/event")
async def game_event(game_id: int, body: EventRequest) -> dict[str, Any]:
    try:
        event_id = await asyncio.to_thread(
            storage.record_event,
            game_id,
            body.type,
            body.payload,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="Não foi possível registrar o evento para essa partida.",
        ) from exc
    return {"ok": True, "event_id": event_id}


@app.get("/review/stats")
async def review_stats(
    recent_limit: int = Query(10, ge=1, le=30),
) -> dict[str, Any]:
    return await asyncio.to_thread(storage.overall_stats, recent_limit)


@app.post("/review/feedback")
async def review_feedback(
    body: ReviewFeedbackRequest,
    request: Request,
) -> StreamingResponse:
    stats = await asyncio.to_thread(storage.overall_stats, 10)
    prompt = build_review_prompt(stats, body.student_level)
    return await _streaming_agy_response(prompt, request)


@app.post("/review/game/{game_id}/feedback")
async def review_game_feedback(
    game_id: int,
    body: ReviewGameFeedbackRequest,
    request: Request,
) -> StreamingResponse:
    game_data = await asyncio.to_thread(storage.get_game, game_id)
    if game_data is None:
        raise HTTPException(status_code=404, detail="Partida não encontrada.")
    prompt = build_game_review_prompt(game_data, body.student_level)
    return await _streaming_agy_response(prompt, request)


@app.post("/move-and-analyze")
async def move_and_analyze(body: MoveAndAnalyzeRequest) -> dict[str, Any]:
    try:
        result = await asyncio.to_thread(
            _move_and_analyze_sync, body.fen, body.move, body.level
        )
    except MoveValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except StockfishUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except StockfishFailure as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if body.game_id is not None:
        try:
            stored = await asyncio.to_thread(
                storage.record_turn,
                body.game_id,
                result,
                body.level,
            )
            result["stored"] = bool(stored)
        except Exception:
            logger.exception("Falha ao gravar a partida %s no SQLite", body.game_id)
            result["stored"] = False
    else:
        result["stored"] = False

    return result


@app.post("/chat")
async def chat(body: ChatRequest, request: Request) -> StreamingResponse:
    prompt = build_trainer_prompt(body)
    return await _streaming_agy_response(prompt, request)
