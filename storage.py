"""Persistência local em SQLite para partidas, lances e eventos."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import chess
import chess.pgn

CLASSIFICATIONS = ("Excelente", "Bom", "Imprecisão", "Erro", "Gafe")
BAD_CLASSIFICATIONS = {"Imprecisão", "Erro", "Gafe"}
CLASSIFICATION_WEIGHTS = {
    "Excelente": 1.0,
    "Bom": 0.8,
    "Imprecisão": 0.5,
    "Erro": 0.2,
    "Gafe": 0.0,
}
PIECE_LABELS = {
    "p": "peão",
    "n": "cavalo",
    "b": "bispo",
    "r": "torre",
    "q": "dama",
    "k": "rei",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _empty_counts() -> dict[str, int]:
    return {label: 0 for label in CLASSIFICATIONS}


def _accuracy(counts: dict[str, int]) -> float | None:
    analyzed = sum(counts.values())
    if analyzed == 0:
        return None
    weighted = sum(
        CLASSIFICATION_WEIGHTS[label] * count
        for label, count in counts.items()
        if label in CLASSIFICATION_WEIGHTS
    )
    return round(100.0 * weighted / analyzed, 1)


def _summarize_moves(moves: list[dict[str, Any]]) -> dict[str, Any]:
    counts = _empty_counts()
    analyzed = 0
    total_cp_loss = 0
    user_plies = 0

    for move in moves:
        if move.get("actor") != "user" or move.get("undone"):
            continue
        user_plies += 1
        classification = move.get("classification")
        if classification in counts:
            counts[classification] += 1
            analyzed += 1
        if isinstance(move.get("cp_loss"), int):
            total_cp_loss += move["cp_loss"]

    return {
        "user_plies": user_plies,
        "analyzed_user_moves": analyzed,
        "classifications": counts,
        "avg_cp_loss": round(total_cp_loss / analyzed, 1) if analyzed else None,
        "accuracy": _accuracy(counts),
        "bad_moves": sum(counts[label] for label in BAD_CLASSIFICATIONS),
        "blunders": counts["Erro"] + counts["Gafe"],
    }


class Storage:
    """Cada operação abre sua própria conexão, segura para uso em to_thread."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS games (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    start_fen TEXT NOT NULL,
                    current_fen TEXT NOT NULL,
                    user_color TEXT NOT NULL DEFAULT 'white',
                    bot_level INTEGER NOT NULL DEFAULT 3,
                    student_level TEXT NOT NULL DEFAULT 'beginner',
                    result TEXT,
                    game_over INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS moves (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                    ply INTEGER NOT NULL,
                    move_number INTEGER NOT NULL,
                    color TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    san TEXT NOT NULL,
                    uci TEXT NOT NULL,
                    fen_before TEXT NOT NULL,
                    fen_after TEXT NOT NULL,
                    classification TEXT,
                    cp_loss INTEGER,
                    eval_before TEXT,
                    eval_after TEXT,
                    best_move_uci TEXT,
                    best_move_san TEXT,
                    pv_json TEXT,
                    refutation_json TEXT,
                    analysis_json TEXT,
                    undone INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_moves_game
                    ON moves(game_id, ply, id);
                CREATE INDEX IF NOT EXISTS idx_moves_game_active
                    ON moves(game_id, undone, actor);

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id INTEGER REFERENCES games(id) ON DELETE CASCADE,
                    type TEXT NOT NULL,
                    payload_json TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_events_game
                    ON events(game_id, id);

                CREATE TABLE IF NOT EXISTS app_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            conn.commit()
        finally:
            conn.close()

    def _set_current_game(self, conn: sqlite3.Connection, game_id: int) -> None:
        conn.execute(
            """
            INSERT INTO app_state (key, value, updated_at)
            VALUES ('current_game_id', ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (str(game_id), utc_now()),
        )

    def _clear_current_game(
        self,
        conn: sqlite3.Connection,
        game_id: int | None = None,
    ) -> None:
        if game_id is None:
            conn.execute("DELETE FROM app_state WHERE key = 'current_game_id'")
            return
        conn.execute(
            """
            DELETE FROM app_state
            WHERE key = 'current_game_id' AND value = ?
            """,
            (str(game_id),),
        )

    def set_current_game(self, game_id: int) -> bool:
        conn = self._connect()
        try:
            with conn:
                row = conn.execute(
                    "SELECT id FROM games WHERE id = ?", (game_id,)
                ).fetchone()
                if row is None:
                    return False
                self._set_current_game(conn, game_id)
        finally:
            conn.close()
        return True

    def get_current_game(self) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT value FROM app_state
                WHERE key = 'current_game_id'
                """
            ).fetchone()
            if row is None:
                return None

            try:
                game_id = int(row["value"])
            except (TypeError, ValueError):
                with conn:
                    self._clear_current_game(conn)
                return None

            game = conn.execute(
                "SELECT id FROM games WHERE id = ?", (game_id,)
            ).fetchone()
            if game is None:
                with conn:
                    self._clear_current_game(conn)
                return None
        finally:
            conn.close()

        return self.get_game(game_id)

    def create_game(
        self,
        level: int = 3,
        student_level: str = "beginner",
        start_fen: str = chess.STARTING_FEN,
        user_color: str = "white",
    ) -> dict[str, Any]:
        now = utc_now()
        conn = self._connect()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO games
                        (started_at, start_fen, current_fen, user_color,
                         bot_level, student_level)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (now, start_fen, start_fen, user_color, level, student_level),
                )
                game_id = int(cursor.lastrowid)
                conn.execute(
                    """
                    INSERT INTO events (game_id, type, payload_json, created_at)
                    VALUES (?, 'game_started', ?, ?)
                    """,
                    (
                        game_id,
                        json.dumps(
                            {
                                "level": level,
                                "student_level": student_level,
                                "start_fen": start_fen,
                            },
                            ensure_ascii=False,
                        ),
                        now,
                    ),
                )
                self._set_current_game(conn, game_id)
        finally:
            conn.close()

        return {
            "id": game_id,
            "started_at": now,
            "fen": start_fen,
            "level": level,
            "student_level": student_level,
            "user_color": user_color,
        }

    def record_turn(
        self,
        game_id: int,
        analysis: dict[str, Any],
        level: int,
    ) -> dict[str, int] | None:
        user_move = analysis.get("user_move") or {}
        if not user_move.get("uci") or not user_move.get("san"):
            return None

        fen_before = analysis.get("fen_before")
        fen_after_user = analysis.get("fen_after_user")
        if not fen_before or not fen_after_user:
            return None

        try:
            before_board = chess.Board(fen_before)
        except ValueError:
            return None

        evaluation = analysis.get("evaluation") or {}
        eval_before = (evaluation.get("before") or {}).get("label")
        eval_after_user = (evaluation.get("after_user") or {}).get("label")
        eval_after_bot = (evaluation.get("after_bot") or {}).get("label")

        bot_move = analysis.get("bot_move") or {}
        fen_after_bot = analysis.get("fen_after_bot") or fen_after_user
        result = analysis.get("result")
        game_over = bool(analysis.get("game_over"))
        now = utc_now()

        conn = self._connect()
        try:
            with conn:
                game = conn.execute(
                    "SELECT id FROM games WHERE id = ?", (game_id,)
                ).fetchone()
                if game is None:
                    return None

                next_ply = int(
                    conn.execute(
                        "SELECT COALESCE(MAX(ply), 0) + 1 FROM moves WHERE game_id = ?",
                        (game_id,),
                    ).fetchone()[0]
                )

                user_cursor = conn.execute(
                    """
                    INSERT INTO moves
                        (game_id, ply, move_number, color, actor, san, uci,
                         fen_before, fen_after, classification, cp_loss,
                         eval_before, eval_after, best_move_uci, best_move_san,
                         pv_json, refutation_json, analysis_json, created_at)
                    VALUES (?, ?, ?, ?, 'user', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        game_id,
                        next_ply,
                        before_board.fullmove_number,
                        "w" if before_board.turn == chess.WHITE else "b",
                        user_move["san"],
                        user_move["uci"],
                        fen_before,
                        fen_after_user,
                        analysis.get("classification"),
                        analysis.get("cp_loss"),
                        eval_before,
                        eval_after_user,
                        (analysis.get("best_move") or {}).get("uci"),
                        (analysis.get("best_move") or {}).get("san"),
                        json.dumps(analysis.get("pv") or [], ensure_ascii=False),
                        json.dumps(
                            analysis.get("refutation_pv") or [],
                            ensure_ascii=False,
                        ),
                        json.dumps(analysis, ensure_ascii=False),
                        now,
                    ),
                )
                user_move_id = int(user_cursor.lastrowid)

                bot_move_id = None
                if bot_move.get("uci") and bot_move.get("san"):
                    try:
                        after_user_board = chess.Board(fen_after_user)
                    except ValueError:
                        after_user_board = None

                    if after_user_board is not None:
                        bot_cursor = conn.execute(
                            """
                            INSERT INTO moves
                                (game_id, ply, move_number, color, actor, san, uci,
                                 fen_before, fen_after, eval_before, eval_after,
                                 analysis_json, created_at)
                            VALUES (?, ?, ?, ?, 'bot', ?, ?, ?, ?, ?, ?, NULL, ?)
                            """,
                            (
                                game_id,
                                next_ply + 1,
                                after_user_board.fullmove_number,
                                "w"
                                if after_user_board.turn == chess.WHITE
                                else "b",
                                bot_move["san"],
                                bot_move["uci"],
                                fen_after_user,
                                fen_after_bot,
                                eval_after_user,
                                eval_after_bot,
                                now,
                            ),
                        )
                        bot_move_id = int(bot_cursor.lastrowid)

                conn.execute(
                    """
                    UPDATE games
                    SET current_fen = ?,
                        bot_level = ?,
                        result = ?,
                        game_over = ?,
                        ended_at = CASE
                            WHEN ? = 1 THEN COALESCE(ended_at, ?)
                            ELSE ended_at
                        END
                    WHERE id = ?
                    """,
                    (
                        fen_after_bot,
                        level,
                        result,
                        1 if game_over else 0,
                        1 if game_over else 0,
                        now,
                        game_id,
                    ),
                )

                if game_over:
                    conn.execute(
                        """
                        INSERT INTO events (game_id, type, payload_json, created_at)
                        VALUES (?, 'game_over', ?, ?)
                        """,
                        (
                            game_id,
                            json.dumps(
                                {"result": result, "fen": fen_after_bot},
                                ensure_ascii=False,
                            ),
                            now,
                        ),
                    )
        finally:
            conn.close()

        return {
            "user_move_id": user_move_id,
            "bot_move_id": bot_move_id or 0,
            "ply": next_ply,
        }

    def end_game(
        self,
        game_id: int,
        reason: str = "finished",
        result: str | None = None,
    ) -> bool:
        now = utc_now()
        conn = self._connect()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    UPDATE games
                    SET ended_at = COALESCE(ended_at, ?),
                        result = COALESCE(result, ?),
                        game_over = 1
                    WHERE id = ?
                    """,
                    (now, result, game_id),
                )
                if cursor.rowcount == 0:
                    return False
                conn.execute(
                    """
                    INSERT INTO events (game_id, type, payload_json, created_at)
                    VALUES (?, 'game_ended', ?, ?)
                    """,
                    (
                        game_id,
                        json.dumps(
                            {"reason": reason, "result": result},
                            ensure_ascii=False,
                        ),
                        now,
                    ),
                )
                self._clear_current_game(conn, game_id)
        finally:
            conn.close()
        return True

    def record_undo(
        self,
        game_id: int,
        plies: int,
        fen: str,
        pgn: str | None = None,
        reason: str = "undo",
    ) -> dict[str, Any]:
        plies = max(1, min(int(plies), 8))
        now = utc_now()
        conn = self._connect()
        try:
            with conn:
                game = conn.execute(
                    "SELECT id, start_fen FROM games WHERE id = ?", (game_id,)
                ).fetchone()
                if game is None:
                    return {"marked": 0, "moves": []}

                active_rows = conn.execute(
                    """
                    SELECT id, san, actor, classification, fen_before, fen_after
                    FROM moves
                    WHERE game_id = ? AND undone = 0
                    ORDER BY ply, id
                    """,
                    (game_id,),
                ).fetchall()

                if not active_rows:
                    return {"marked": 0, "moves": []}

                try:
                    chess.Board(fen)
                    new_fen = fen
                except ValueError:
                    new_fen = active_rows[-1]["fen_before"]

                # O alvo é o FEN que o tabuleiro local alcançou depois do
                # desfazer. Isso evita marcar lances errados quando a análise
                # ainda estava em voo. O número de plies é apenas fallback.
                target_key = " ".join(new_fen.split(" ")[:4])
                if new_fen == game["start_fen"]:
                    rows = active_rows
                else:
                    cutoff = -1
                    for index, row in enumerate(active_rows):
                        after_key = " ".join(str(row["fen_after"]).split(" ")[:4])
                        if row["fen_after"] == new_fen or after_key == target_key:
                            cutoff = index

                    if cutoff >= 0:
                        rows = active_rows[cutoff + 1 :]
                    else:
                        rows = active_rows[-plies:]

                if not rows:
                    return {"marked": 0, "moves": []}

                ids = [int(row["id"]) for row in rows]
                placeholders = ",".join("?" for _ in ids)
                conn.execute(
                    f"UPDATE moves SET undone = 1 WHERE id IN ({placeholders})",
                    ids,
                )

                conn.execute(
                    """
                    UPDATE games
                    SET current_fen = ?,
                        ended_at = NULL,
                        result = NULL,
                        game_over = 0
                    WHERE id = ?
                    """,
                    (new_fen, game_id),
                )
                conn.execute(
                    """
                    INSERT INTO events (game_id, type, payload_json, created_at)
                    VALUES (?, 'undo', ?, ?)
                    """,
                    (
                        game_id,
                        json.dumps(
                            {
                                "reason": reason,
                                "requested_plies": plies,
                                "marked": len(rows),
                                "moves": [
                                    {
                                        "san": row["san"],
                                        "actor": row["actor"],
                                        "classification": row["classification"],
                                    }
                                    for row in rows
                                ],
                                "fen": new_fen,
                                "pgn": (pgn or "")[-4000:],
                            },
                            ensure_ascii=False,
                        ),
                        now,
                    ),
                )
                self._set_current_game(conn, game_id)
        finally:
            conn.close()

        return {
            "marked": len(rows),
            "moves": [row["san"] for row in rows],
        }

    def record_event(
        self,
        game_id: int | None,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> int:
        now = utc_now()
        conn = self._connect()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO events (game_id, type, payload_json, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        game_id,
                        event_type,
                        json.dumps(payload or {}, ensure_ascii=False),
                        now,
                    ),
                )
                return int(cursor.lastrowid)
        finally:
            conn.close()

    def list_games(
        self,
        limit: int = 30,
        include_empty: bool = False,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        conn = self._connect()
        try:
            where = ""
            if not include_empty:
                where = (
                    "WHERE EXISTS ("
                    "SELECT 1 FROM moves m WHERE m.game_id = g.id"
                    ")"
                )
            games = conn.execute(
                f"""
                SELECT g.*,
                    (SELECT COUNT(*) FROM moves m
                     WHERE m.game_id = g.id AND m.undone = 0) AS plies,
                    (SELECT COUNT(*) FROM moves m
                     WHERE m.game_id = g.id AND m.undone = 1) AS undone_plies,
                    (SELECT COUNT(*) FROM moves m
                     WHERE m.game_id = g.id AND m.undone = 0
                       AND m.actor = 'user') AS user_plies,
                    (SELECT COUNT(*) FROM moves m
                     WHERE m.game_id = g.id AND m.undone = 0
                       AND m.actor = 'user' AND m.classification IS NOT NULL)
                        AS analyzed_user_moves,
                    (SELECT COALESCE(SUM(m.cp_loss), 0) FROM moves m
                     WHERE m.game_id = g.id AND m.undone = 0
                       AND m.actor = 'user' AND m.cp_loss IS NOT NULL)
                        AS total_cp_loss
                FROM games g
                {where}
                ORDER BY g.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

            result: list[dict[str, Any]] = []
            for game in games:
                counts = _empty_counts()
                classification_rows = conn.execute(
                    """
                    SELECT classification, COUNT(*) AS total
                    FROM moves
                    WHERE game_id = ? AND undone = 0 AND actor = 'user'
                      AND classification IS NOT NULL
                    GROUP BY classification
                    """,
                    (game["id"],),
                ).fetchall()
                for row in classification_rows:
                    if row["classification"] in counts:
                        counts[row["classification"]] = int(row["total"])

                analyzed = int(game["analyzed_user_moves"])
                avg_cp_loss = (
                    round(int(game["total_cp_loss"]) / analyzed, 1)
                    if analyzed
                    else None
                )
                result.append(
                    {
                        "id": int(game["id"]),
                        "started_at": game["started_at"],
                        "ended_at": game["ended_at"],
                        "start_fen": game["start_fen"],
                        "current_fen": game["current_fen"],
                        "user_color": game["user_color"],
                        "level": int(game["bot_level"]),
                        "student_level": game["student_level"],
                        "result": game["result"],
                        "game_over": bool(game["game_over"]),
                        "plies": int(game["plies"]),
                        "undone_plies": int(game["undone_plies"]),
                        "user_plies": int(game["user_plies"]),
                        "analyzed_user_moves": analyzed,
                        "avg_cp_loss": avg_cp_loss,
                        "accuracy": _accuracy(counts),
                        "classifications": counts,
                        "bad_moves": sum(counts[label] for label in BAD_CLASSIFICATIONS),
                        "blunders": counts["Erro"] + counts["Gafe"],
                    }
                )
            return result
        finally:
            conn.close()

    def get_game(self, game_id: int) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            game = conn.execute(
                "SELECT * FROM games WHERE id = ?", (game_id,)
            ).fetchone()
            if game is None:
                return None

            active_rows = conn.execute(
                """
                SELECT * FROM moves
                WHERE game_id = ? AND undone = 0
                ORDER BY ply, id
                """,
                (game_id,),
            ).fetchall()
            undone_rows = conn.execute(
                """
                SELECT * FROM moves
                WHERE game_id = ? AND undone = 1
                ORDER BY id DESC
                """,
                (game_id,),
            ).fetchall()
            event_rows = conn.execute(
                """
                SELECT * FROM events
                WHERE game_id = ?
                ORDER BY id DESC
                LIMIT 100
                """,
                (game_id,),
            ).fetchall()

            active_moves = [self._decode_move(row) for row in active_rows]
            undone_moves = [self._decode_move(row) for row in undone_rows]
            events = [self._decode_event(row) for row in event_rows]
            summary = _summarize_moves(active_moves)
            summary.update(
                {
                    "plies": len(active_moves),
                    "undone_moves": len(undone_moves),
                }
            )

            return {
                "game": {
                    "id": int(game["id"]),
                    "started_at": game["started_at"],
                    "ended_at": game["ended_at"],
                    "start_fen": game["start_fen"],
                    "current_fen": game["current_fen"],
                    "user_color": game["user_color"],
                    "level": int(game["bot_level"]),
                    "student_level": game["student_level"],
                    "result": game["result"],
                    "game_over": bool(game["game_over"]),
                },
                "summary": summary,
                "moves": active_moves,
                "undone_moves": undone_moves,
                "events": events,
                "pgn": self._build_pgn(game, active_moves),
            }
        finally:
            conn.close()

    def _build_pgn(
        self,
        game: sqlite3.Row,
        moves: list[dict[str, Any]],
    ) -> str:
        try:
            board = chess.Board(game["start_fen"])
        except ValueError:
            return ""

        pgn = chess.pgn.Game()
        pgn.headers["Event"] = "Treino local"
        pgn.headers["Site"] = "xadrez local"
        pgn.headers["Date"] = game["started_at"][:10].replace("-", ".")
        pgn.headers["White"] = "Aluno" if game["user_color"] == "white" else "Stockfish"
        pgn.headers["Black"] = "Stockfish" if game["user_color"] == "white" else "Aluno"
        pgn.headers["Result"] = game["result"] or "*"
        if game["start_fen"] != chess.STARTING_FEN:
            pgn.headers["SetUp"] = "1"
            pgn.headers["FEN"] = game["start_fen"]

        node: chess.pgn.GameNode = pgn
        for move in moves:
            try:
                parsed = board.parse_san(move["san"])
                board.push(parsed)
                node = node.add_variation(parsed)
            except (ValueError, AssertionError):
                break
        return str(pgn)

    @staticmethod
    def _decode_move(row: sqlite3.Row) -> dict[str, Any]:
        def _load_json(value: str | None) -> Any:
            if not value:
                return None
            try:
                return json.loads(value)
            except (TypeError, json.JSONDecodeError):
                return None

        return {
            "id": int(row["id"]),
            "ply": int(row["ply"]),
            "move_number": int(row["move_number"]),
            "color": row["color"],
            "actor": row["actor"],
            "san": row["san"],
            "uci": row["uci"],
            "fen_before": row["fen_before"],
            "fen_after": row["fen_after"],
            "classification": row["classification"],
            "cp_loss": row["cp_loss"],
            "eval_before": row["eval_before"],
            "eval_after": row["eval_after"],
            "best_move_uci": row["best_move_uci"],
            "best_move_san": row["best_move_san"],
            "pv": _load_json(row["pv_json"]) or [],
            "refutation_pv": _load_json(row["refutation_json"]) or [],
            "undone": bool(row["undone"]),
            "created_at": row["created_at"],
        }

    @staticmethod
    def _decode_event(row: sqlite3.Row) -> dict[str, Any]:
        try:
            payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
        except (TypeError, json.JSONDecodeError):
            payload = {}
        return {
            "id": int(row["id"]),
            "type": row["type"],
            "payload": payload,
            "created_at": row["created_at"],
        }

    def overall_stats(self, recent_limit: int = 10) -> dict[str, Any]:
        recent_games = self.list_games(limit=recent_limit, include_empty=False)
        all_games = self.list_games(limit=500, include_empty=False)

        counts = _empty_counts()
        total_analyzed = 0
        total_cp_loss = 0
        for game in all_games:
            for label, count in game["classifications"].items():
                counts[label] += count
            analyzed = game["analyzed_user_moves"]
            total_analyzed += analyzed
            if game["avg_cp_loss"] is not None:
                total_cp_loss += game["avg_cp_loss"] * analyzed

        avg_cp_loss = round(total_cp_loss / total_analyzed, 1) if total_analyzed else None
        accuracy = _accuracy(counts)

        phase_stats = {
            "abertura": {"total": 0, "bad": 0},
            "meio-jogo": {"total": 0, "bad": 0},
            "final": {"total": 0, "bad": 0},
        }
        piece_stats: dict[str, dict[str, Any]] = {}
        opening_sequences: dict[str, int] = {}
        games_openings: dict[int, list[str]] = {}

        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT m.game_id, m.ply, m.uci, m.fen_before,
                       m.classification, m.san
                FROM moves m
                JOIN games g ON g.id = m.game_id
                WHERE m.actor = 'user' AND m.undone = 0
                  AND m.classification IS NOT NULL
                ORDER BY m.game_id, m.ply, m.id
                """
            ).fetchall()
        finally:
            conn.close()

        for row in rows:
            classification = row["classification"]
            is_bad = classification in BAD_CLASSIFICATIONS
            ply = int(row["ply"])
            phase = "abertura" if ply <= 20 else "meio-jogo" if ply <= 60 else "final"
            phase_stats[phase]["total"] += 1
            if is_bad:
                phase_stats[phase]["bad"] += 1

            piece_symbol = self._piece_at(row["fen_before"], row["uci"])
            if piece_symbol:
                label = PIECE_LABELS.get(piece_symbol, piece_symbol)
                stats = piece_stats.setdefault(label, {"total": 0, "bad": 0})
                stats["total"] += 1
                if is_bad:
                    stats["bad"] += 1

            first_moves = games_openings.setdefault(int(row["game_id"]), [])
            if len(first_moves) < 3:
                first_moves.append(row["san"])

        for sequence in games_openings.values():
            line = " ".join(sequence)
            opening_sequences[line] = opening_sequences.get(line, 0) + 1

        for stats in phase_stats.values():
            stats["bad_rate"] = (
                round(100.0 * stats["bad"] / stats["total"], 1)
                if stats["total"]
                else None
            )
        for stats in piece_stats.values():
            stats["bad_rate"] = (
                round(100.0 * stats["bad"] / stats["total"], 1)
                if stats["total"]
                else None
            )

        latest = [game for game in all_games[:10] if game["accuracy"] is not None]
        previous = [game for game in all_games[10:20] if game["accuracy"] is not None]

        def average(values: list[float]) -> float | None:
            return round(sum(values) / len(values), 1) if values else None

        def average_loss(games: list[dict[str, Any]]) -> float | None:
            weighted = [
                (game["avg_cp_loss"], game["analyzed_user_moves"])
                for game in games
                if game["avg_cp_loss"] is not None and game["analyzed_user_moves"]
            ]
            total = sum(weight for _, weight in weighted)
            if not total:
                return None
            return round(sum(value * weight for value, weight in weighted) / total, 1)

        openings = sorted(
            (
                {"line": line, "count": count}
                for line, count in opening_sequences.items()
            ),
            key=lambda item: (-item["count"], item["line"]),
        )[:8]

        return {
            "games": len(all_games),
            "analyzed_user_moves": total_analyzed,
            "classifications": counts,
            "avg_cp_loss": avg_cp_loss,
            "accuracy": accuracy,
            "bad_moves": sum(counts[label] for label in BAD_CLASSIFICATIONS),
            "blunders": counts["Erro"] + counts["Gafe"],
            "bad_rate": (
                round(100.0 * sum(counts[label] for label in BAD_CLASSIFICATIONS) / total_analyzed, 1)
                if total_analyzed
                else None
            ),
            "phases": phase_stats,
            "pieces": piece_stats,
            "openings": openings,
            "trend": {
                "recent_accuracy": average(
                    [game["accuracy"] for game in latest if game["accuracy"] is not None]
                ),
                "previous_accuracy": average(
                    [game["accuracy"] for game in previous if game["accuracy"] is not None]
                ),
                "recent_avg_cp_loss": average_loss(latest),
                "previous_avg_cp_loss": average_loss(previous),
            },
            "recent_games": recent_games,
        }

    @staticmethod
    def _piece_at(fen: str, uci: str) -> str | None:
        if not isinstance(fen, str) or not isinstance(uci, str) or len(uci) < 4:
            return None
        try:
            board = chess.Board(fen)
            square = chess.parse_square(uci[:2])
            piece = board.piece_at(square)
        except (ValueError, AssertionError):
            return None
        return piece.symbol().lower() if piece else None
