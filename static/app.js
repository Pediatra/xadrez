import { Chess } from "https://cdn.jsdelivr.net/npm/chess.js@1.4.0/+esm";
import { Chessground } from "https://cdn.jsdelivr.net/npm/chessground@9.1.1/dist/chessground.min.js";

const START_FEN = new Chess().fen();
const NEUTRAL_EVAL = Object.freeze({
  type: "cp",
  value: 0,
  label: "0.00",
  white_cp: 0,
});
const BAD_CLASSIFICATIONS = new Set(["Imprecisão", "Erro", "Gafe"]);
const QUALITY_SLUGS = {
  Excelente: "excelente",
  Bom: "bom",
  Imprecisão: "imprecisao",
  Erro: "erro",
  Gafe: "gafe",
};
const TRAINER_LEVELS = new Set(["beginner", "intermediate", "advanced"]);
const PIECE_NAMES = {
  p: "peão",
  n: "cavalo",
  b: "bispo",
  r: "torre",
  q: "dama",
  k: "rei",
};
const FEMININE_PIECES = new Set(["q", "r"]);
const COACH_MAX_PLIES = 8;

const $ = (selector) => document.querySelector(selector);

const ui = {
  board: $("#board"),
  boardShell: $("#board-shell"),
  boardFilesTop: $("#board-files-top"),
  boardFilesBottom: $("#board-files-bottom"),
  boardRanksLeft: $("#board-ranks-left"),
  boardRanksRight: $("#board-ranks-right"),
  boardOverlay: $("#board-overlay"),
  boardOverlayText: $("#board-overlay-text"),
  evalBar: $("#eval-bar"),
  evalWhite: $("#eval-white"),
  evalLabel: $("#eval-label"),
  turnPill: $("#turn-pill"),
  qualityBadge: $("#quality-badge"),
  newGame: $("#new-game"),
  flipBoard: $("#flip-board"),
  undoMove: $("#undo-move"),
  botLevel: $("#bot-level"),
  engineWarning: $("#engine-warning"),
  moveList: $("#move-list"),
  moveCount: $("#move-count"),
  chatMessages: $("#chat-messages"),
  chatForm: $("#chat-form"),
  chatInput: $("#chat-input"),
  chatSend: $("#chat-send"),
  chatStatus: $("#chat-status"),
  appLayout: $(".app-layout"),
  trainerLevel: $("#trainer-level"),
  coachPanel: $("#coach-panel"),
  coachBoard: $("#coach-board"),
  coachClose: $("#coach-close"),
  coachTabPlayed: $("#coach-tab-played"),
  coachTabBest: $("#coach-tab-best"),
  coachCaption: $("#coach-caption"),
  coachStart: $("#coach-start"),
  coachPrev: $("#coach-prev"),
  coachPlay: $("#coach-play"),
  coachNext: $("#coach-next"),
  coachStep: $("#coach-step"),
  coachMoves: $("#coach-moves"),
  viewTrainer: $("#view-trainer"),
  viewReview: $("#view-review"),
  trainerView: $("#trainer-view"),
  reviewView: $("#review-view"),
  reviewCount: $("#review-count"),
  reviewRefresh: $("#review-refresh"),
  reviewOverall: $("#review-overall"),
  reviewStats: $("#review-stats"),
  reviewGamesCount: $("#review-games-count"),
  reviewGames: $("#review-games"),
  reviewEmpty: $("#review-empty"),
  reviewContent: $("#review-content"),
  reviewDetailTitle: $("#review-detail-title"),
  reviewGameSubtitle: $("#review-game-subtitle"),
  reviewGameFeedback: $("#review-game-feedback"),
  reviewGameSummary: $("#review-game-summary"),
  reviewMoves: $("#review-moves"),
  reviewUndoneWrap: $("#review-undone-wrap"),
  reviewUndone: $("#review-undone"),
  reviewAiMessages: $("#review-ai-messages"),
  reviewAiStatus: $("#review-ai-status"),
};

const state = {
  game: new Chess(),
  orientation: "white",
  level: 3,
  boardLocked: false,
  analyzing: false,
  agyAvailable: null,
  chatBusy: false,
  chatQueue: [],
  moveController: null,
  arrowsTimer: null,
  evalByFen: new Map(),
  lastAnalysis: null,
  trainerLevel: "beginner",
  gameId: null,
  gamePromise: null,
  gameEpoch: 0,
  reviewBusy: false,
  reviewLoading: false,
  reviewGameId: null,
  reviewStats: null,
};

const coach = {
  ground: null,
  lines: { played: null, best: null },
  active: "played",
  step: 0,
  playing: false,
  timer: null,
};

let ground = null;
let scrollFrame = null;
let reviewScrollFrame = null;

function fenKey(fen) {
  return fen.split(" ").slice(0, 4).join(" ");
}

function delay(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function startGameRecord() {
  const response = await fetch("/games", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      level: state.level,
      student_level: state.trainerLevel,
    }),
  });
  if (!response.ok) {
    throw new Error(await readHttpError(response));
  }
  return response.json();
}

function ensureGameRecord() {
  if (state.gameId) return Promise.resolve(state.gameId);
  if (state.gamePromise) return state.gamePromise;

  const epoch = state.gameEpoch;
  state.gamePromise = startGameRecord()
    .then((game) => {
      if (epoch !== state.gameEpoch) return null;
      state.gameId = game.id;
      return game.id;
    })
    .finally(() => {
      if (epoch === state.gameEpoch) {
        state.gamePromise = null;
      }
    });
  return state.gamePromise;
}

async function endGameRecord(gameId, reason, result = null) {
  if (!gameId) return;
  try {
    await fetch(`/games/${gameId}/end`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason, result }),
    });
  } catch (error) {
    console.warn("Não foi possível encerrar a partida no histórico:", error);
  }
}

async function recordUndo(gameId, plies) {
  if (!gameId) return;
  try {
    await fetch(`/games/${gameId}/undo`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        plies,
        fen: state.game.fen(),
        pgn: state.game.pgn(),
        reason: "undo",
      }),
    });
  } catch (error) {
    console.warn("Não foi possível registrar o desfazer:", error);
  }
}

async function recordGameEvent(type, payload = {}) {
  if (!state.gameId) return;
  try {
    await fetch(`/games/${state.gameId}/event`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type, payload }),
    });
  } catch (error) {
    console.warn("Não foi possível registrar o evento:", error);
  }
}

function canUserMove() {
  return (
    !state.boardLocked &&
    !state.analyzing &&
    !state.game.isGameOver() &&
    state.game.turn() === "w"
  );
}

function legalDests() {
  const dests = new Map();
  if (!canUserMove()) return dests;

  for (const move of state.game.moves({ verbose: true })) {
    const targets = dests.get(move.from) ?? [];
    if (!targets.includes(move.to)) targets.push(move.to);
    dests.set(move.from, targets);
  }
  return dests;
}

function lastMoveFromHistory() {
  const history = state.game.history({ verbose: true });
  const last = history.at(-1);
  return last ? [last.from, last.to] : undefined;
}

function loadTrainerLevel() {
  try {
    const saved = window.localStorage.getItem("xadrez-trainer-level");
    if (TRAINER_LEVELS.has(saved)) return saved;
  } catch {
    // localStorage pode estar bloqueado; usa o padrão iniciante.
  }
  return "beginner";
}

function saveTrainerLevel(level) {
  try {
    window.localStorage.setItem("xadrez-trainer-level", level);
  } catch {
    // Sem localStorage, a escolha vale só para esta sessão.
  }
}

function init() {
  state.evalByFen.set(fenKey(START_FEN), NEUTRAL_EVAL);
  state.trainerLevel = loadTrainerLevel();
  ui.trainerLevel.value = state.trainerLevel;
  renderBoardCoords();

  ground = Chessground(ui.board, {
    fen: state.game.fen(),
    orientation: state.orientation,
    turnColor: "white",
    coordinates: false,
    animation: { enabled: true, duration: 230 },
    highlight: { lastMove: true, check: true },
    movable: {
      free: false,
      color: "white",
      dests: legalDests(),
      showDests: true,
      events: { after: handleBoardMove },
    },
    draggable: { enabled: true, showGhost: true },
    selectable: { enabled: true },
    premovable: { enabled: false },
    drawable: { enabled: true, visible: true },
  });

  bindEvents();
  renderBoard();
  renderMoves();
  updateEval(NEUTRAL_EVAL);
  updateChatControls();
  updateReviewControls();
  void checkHealth();
  void ensureGameRecord().catch((error) => {
    console.warn("Não foi possível iniciar o histórico SQLite:", error);
    appendChatNote(
      "⚠️ O histórico SQLite não iniciou. O jogo funciona, mas esta partida não será salva.",
    );
  });
}

function bindEvents() {
  ui.newGame.addEventListener("click", startNewGame);
  ui.flipBoard.addEventListener("click", flipBoard);
  ui.undoMove.addEventListener("click", undoMove);
  ui.botLevel.addEventListener("change", () => {
    state.level = Number.parseInt(ui.botLevel.value, 10) || 3;
    void recordGameEvent("level_change", { level: state.level });
  });

  ui.trainerLevel.addEventListener("change", () => {
    const level = ui.trainerLevel.value;
    state.trainerLevel = TRAINER_LEVELS.has(level) ? level : "beginner";
    saveTrainerLevel(state.trainerLevel);
  });

  ui.coachClose.addEventListener("click", hideCoachBoard);
  ui.coachTabPlayed.addEventListener("click", () => setCoachTab("played"));
  ui.coachTabBest.addEventListener("click", () => setCoachTab("best"));
  ui.coachStart.addEventListener("click", () => {
    stopCoachPlayback();
    coach.step = 0;
    renderCoach();
  });
  ui.coachPrev.addEventListener("click", () => {
    stopCoachPlayback();
    coach.step = Math.max(0, coach.step - 1);
    renderCoach();
  });
  ui.coachNext.addEventListener("click", () => {
    stopCoachPlayback();
    coach.step = Math.min(currentCoachLine()?.moves.length ?? 0, coach.step + 1);
    renderCoach();
  });
  ui.coachPlay.addEventListener("click", toggleCoachPlayback);

  ui.viewTrainer.addEventListener("click", () => setView("trainer"));
  ui.viewReview.addEventListener("click", () => setView("review"));
  ui.reviewRefresh.addEventListener("click", () => void loadReview());
  ui.reviewOverall.addEventListener("click", () =>
    void streamReviewFeedback(
      "/review/feedback",
      { student_level: state.trainerLevel },
      "Feedback geral",
    ),
  );
  ui.reviewGameFeedback.addEventListener("click", () => {
    if (!state.reviewGameId) return;
    void streamReviewFeedback(
      `/review/game/${state.reviewGameId}/feedback`,
      { student_level: state.trainerLevel },
      `Análise da partida #${state.reviewGameId}`,
    );
  });

  ui.chatForm.addEventListener("submit", (event) => {
    event.preventDefault();
    submitChat();
  });
  ui.chatInput.addEventListener("input", updateChatControls);
  ui.chatInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submitChat();
    }
  });

  window.addEventListener("pagehide", () => {
    state.moveController?.abort();
    stopCoachPlayback();
  });
}

const BOARD_FILES = ["a", "b", "c", "d", "e", "f", "g", "h"];
const BOARD_RANKS = ["8", "7", "6", "5", "4", "3", "2", "1"];

function fillCoords(element, values) {
  if (!element) return;
  const spans = values.map((value) => {
    const span = document.createElement("span");
    span.textContent = value;
    return span;
  });
  element.replaceChildren(...spans);
}

function renderBoardCoords() {
  const files =
    state.orientation === "white" ? BOARD_FILES : [...BOARD_FILES].reverse();
  const ranks =
    state.orientation === "white" ? BOARD_RANKS : [...BOARD_RANKS].reverse();

  fillCoords(ui.boardFilesTop, files);
  fillCoords(ui.boardFilesBottom, files);
  fillCoords(ui.boardRanksLeft, ranks);
  fillCoords(ui.boardRanksRight, ranks);
}

function renderBoard(lastMove) {
  if (!ground) return;
  const turnColor = state.game.turn() === "w" ? "white" : "black";

  ground.set({
    fen: state.game.fen(),
    turnColor,
    check: state.game.inCheck() ? turnColor : false,
    lastMove: lastMove ?? undefined,
    movable: {
      color: canUserMove() ? "white" : undefined,
      dests: legalDests(),
    },
  });

  updateTurnPill();
  updateUndoButton();
}

function renderMoves() {
  const history = state.game.history({ verbose: true });
  ui.moveList.replaceChildren();

  if (history.length === 0) {
    const empty = document.createElement("li");
    empty.className = "empty";
    empty.textContent = "Nenhum lance ainda.";
    ui.moveList.append(empty);
    ui.moveCount.textContent = "0";
    return;
  }

  for (let index = 0; index < history.length; index += 2) {
    const item = document.createElement("li");
    const number = document.createElement("span");
    number.className = "move-no";
    number.textContent = `${index / 2 + 1}.`;

    const white = document.createElement("span");
    white.className = "move-san";
    white.textContent = history[index]?.san ?? "…";

    const black = document.createElement("span");
    black.className = "move-san";
    black.textContent = history[index + 1]?.san ?? "…";

    item.append(number, white, black);
    ui.moveList.append(item);
  }

  ui.moveCount.textContent = `${history.length} ${
    history.length === 1 ? "lance" : "lances"
  }`;
  ui.moveList.scrollTop = ui.moveList.scrollHeight;
}

function updateUndoButton() {
  ui.undoMove.disabled =
    state.analyzing || state.game.history().length === 0;
}

function updateTurnPill() {
  let text = "";
  let mode = "your-turn";

  if (state.game.isCheckmate()) {
    text =
      state.game.turn() === "w"
        ? "Xeque-mate · Stockfish venceu"
        : "Xeque-mate · você venceu";
    mode = "over";
  } else if (state.game.isDraw() || state.game.isStalemate()) {
    text = "Fim de jogo · empate";
    mode = "over";
  } else if (state.analyzing) {
    text = "Analisando com Stockfish…";
    mode = "bot-turn";
  } else if (state.boardLocked) {
    text = "Vez do Stockfish…";
    mode = "bot-turn";
  } else if (state.game.turn() === "w") {
    text = "Sua vez (brancas)";
    mode = "your-turn";
  } else {
    text = "Vez das pretas";
    mode = "bot-turn";
  }

  ui.turnPill.textContent = text;
  ui.turnPill.dataset.state = mode;
}

function setAnalyzing(active) {
  state.analyzing = active;
  ui.boardOverlay.hidden = !active;
  ui.boardShell.setAttribute("aria-busy", String(active));
  ui.botLevel.disabled = active;
  if (active) {
    ui.boardOverlayText.textContent = "Analisando seu lance…";
  }
  updateTurnPill();
  updateUndoButton();
}

function updateEval(evaluation) {
  const safe = evaluation && typeof evaluation === "object" ? evaluation : NEUTRAL_EVAL;
  const whiteCp = Number(safe.white_cp ?? 0);
  let share = 50 + 50 * Math.tanh(Math.max(-1200, Math.min(1200, whiteCp)) / 400);

  if (safe.type === "mate") {
    share = whiteCp >= 0 ? 100 : 0;
  }

  ui.evalWhite.style.height = `${share.toFixed(2)}%`;
  ui.evalLabel.textContent = safe.label ?? "0.00";

  const side =
    whiteCp > 20
      ? "vantagem das brancas"
      : whiteCp < -20
        ? "vantagem das pretas"
        : "posição equilibrada";
  ui.evalBar.setAttribute(
    "aria-label",
    `Avaliação: ${safe.label ?? "0.00"}, ${side}`,
  );
}

function recordEval(fen, evaluation) {
  if (!fen || !evaluation) return;
  state.evalByFen.set(fenKey(fen), evaluation);
}

function updateEvalForCurrentFen() {
  const saved = state.evalByFen.get(fenKey(state.game.fen())) ?? NEUTRAL_EVAL;
  updateEval(saved);
}

function showQuality(result) {
  const slug = QUALITY_SLUGS[result.classification];
  if (!slug) {
    hideQualityBadge();
    return;
  }

  const loss = Number(result.cp_loss ?? 0);
  ui.qualityBadge.hidden = false;
  ui.qualityBadge.dataset.quality = slug;
  ui.qualityBadge.textContent = result.classification;
  ui.qualityBadge.title = `Perda de ${(loss / 100).toFixed(2)} peões em relação ao melhor lance.`;
}

function hideQualityBadge() {
  ui.qualityBadge.hidden = true;
  ui.qualityBadge.removeAttribute("data-quality");
  ui.qualityBadge.removeAttribute("title");
}

function drawArrows(playedUci, bestUci) {
  const shapes = [];
  if (typeof playedUci === "string" && playedUci.length >= 4) {
    shapes.push({
      orig: playedUci.slice(0, 2),
      dest: playedUci.slice(2, 4),
      brush: "red",
    });
  }
  if (typeof bestUci === "string" && bestUci.length >= 4) {
    shapes.push({
      orig: bestUci.slice(0, 2),
      dest: bestUci.slice(2, 4),
      brush: "green",
    });
  }
  if (shapes.length > 0) ground.setShapes(shapes);
}

function clearArrows() {
  if (state.arrowsTimer) {
    window.clearTimeout(state.arrowsTimer);
    state.arrowsTimer = null;
  }
  ground?.setShapes([]);
}

function showEngineWarning(message) {
  ui.engineWarning.hidden = false;
  ui.engineWarning.textContent = message;
}

function clearEngineWarning() {
  ui.engineWarning.hidden = true;
  ui.engineWarning.textContent = "";
}

async function readHttpError(response) {
  try {
    const data = await response.json();
    if (typeof data.detail === "string") return data.detail;
    if (Array.isArray(data.detail)) {
      return data.detail.map((item) => item.msg ?? String(item)).join("; ");
    }
  } catch {
    // A resposta não era JSON; cai no texto genérico abaixo.
  }
  return `Falha na requisição (HTTP ${response.status}).`;
}

async function handleBoardMove(orig, dest) {
  if (!canUserMove()) {
    renderBoard(lastMoveFromHistory());
    return;
  }

  const candidates = state.game
    .moves({ square: orig, verbose: true })
    .filter((move) => move.to === dest);
  if (candidates.length === 0) {
    renderBoard(lastMoveFromHistory());
    return;
  }

  const promotion = candidates.some((move) => move.promotion) ? "q" : undefined;
  const fenBefore = state.game.fen();
  let move = null;

  try {
    move = state.game.move({ from: orig, to: dest, promotion });
  } catch {
    move = null;
  }

  if (!move) {
    renderBoard(lastMoveFromHistory());
    return;
  }

  const uci = `${move.from}${move.to}${move.promotion ?? ""}`;
  await analyzeUserMove({
    fenBefore,
    fenAfterUser: state.game.fen(),
    uci,
    lastMove: [orig, dest],
  });
}

async function analyzeUserMove({ fenBefore, fenAfterUser, uci, lastMove }) {
  state.boardLocked = true;
  setAnalyzing(true);
  renderBoard(lastMove);
  renderMoves();
  hideQualityBadge();
  clearArrows();
  hideCoachBoard();

  const controller = new AbortController();
  state.moveController = controller;

  try {
    const gameId = await ensureGameRecord().catch((error) => {
      console.warn("Não foi possível obter o id da partida:", error);
      return null;
    });
    if (controller.signal.aborted) return;

    const response = await fetch("/move-and-analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        fen: fenBefore,
        move: uci,
        level: state.level,
        game_id: gameId,
      }),
      signal: controller.signal,
    });

    if (!response.ok) {
      throw new Error(await readHttpError(response));
    }

    const result = await response.json();
    if (controller.signal.aborted) return;

    await handleAnalysisResult(result, controller.signal);
  } catch (error) {
    if (error.name === "AbortError" || controller.signal.aborted) return;
    rollbackMove(fenAfterUser, error.message);
  } finally {
    if (state.moveController === controller) {
      state.moveController = null;
    }
    setAnalyzing(false);
    if (controller.signal.aborted) {
      renderBoard(lastMoveFromHistory());
    }
  }
}

async function handleAnalysisResult(result, signal) {
  setAnalyzing(false);
  state.lastAnalysis = result;
  state.boardLocked = true;

  updateEval(result.evaluation?.after_user);
  recordEval(result.fen_after_user, result.evaluation?.after_user);
  showQuality(result);

  const isBad = BAD_CLASSIFICATIONS.has(result.classification);
  if (isBad && result.best_move?.uci) {
    showCoachBoard(result);
    drawArrows(result.user_move?.uci, result.best_move.uci);
    enqueueAutoExplanation(result);
    updateTurnPill();

    // Deixa as setas visíveis na posição em que o erro aconteceu.
    await delay(result.bot_move?.uci ? 2000 : 600);
    if (signal.aborted) {
      clearArrows();
      return;
    }
  } else {
    await delay(140);
    if (signal.aborted) return;
  }

  if (result.bot_move?.uci) {
    applyBotMove(result);
  } else {
    state.boardLocked = Boolean(result.game_over);

    if (result.game_over) {
      updateTurnPill();
    } else {
      renderBoard(lastMoveFromHistory());
    }

    const finalEval = result.evaluation?.after_bot ?? result.evaluation?.after_user;
    updateEval(finalEval);
    recordEval(result.fen_after_bot ?? state.game.fen(), finalEval);

    if (isBad && result.best_move?.uci) {
      if (state.arrowsTimer) window.clearTimeout(state.arrowsTimer);
      state.arrowsTimer = window.setTimeout(clearArrows, 6000);
    } else {
      clearArrows();
    }
  }

  if (result.game_over) {
    state.boardLocked = true;
    updateTurnPill();
  }
}

function applyBotMove(result) {
  const uci = result.bot_move?.uci;
  let lastMove = null;

  if (typeof uci === "string" && uci.length >= 4) {
    try {
      const moved = state.game.move({
        from: uci.slice(0, 2),
        to: uci.slice(2, 4),
        promotion: uci.length > 4 ? uci[4] : undefined,
      });
      if (moved) lastMove = [moved.from, moved.to];
    } catch (error) {
      console.error("Não foi possível aplicar o lance do bot:", error);
      showEngineWarning(
        "O Stockfish calculou um lance que o tabuleiro local não conseguiu aplicar. Inicie um novo jogo.",
      );
    }
  }

  clearArrows();
  state.boardLocked = Boolean(result.game_over);
  renderBoard(lastMove ?? lastMoveFromHistory());
  renderMoves();

  const finalEval = result.evaluation?.after_bot ?? result.evaluation?.after_user;
  updateEval(finalEval);
  recordEval(result.fen_after_bot ?? state.game.fen(), finalEval);

  if (result.game_over) updateTurnPill();
}

function capitalizeFirst(text) {
  return text.length > 0 ? text[0].toUpperCase() + text.slice(1) : text;
}

function pieceWithColor(piece, color) {
  const base = PIECE_NAMES[piece] ?? "peça";
  const feminine = FEMININE_PIECES.has(piece);
  const colorName =
    color === "w"
      ? feminine
        ? "branca"
        : "branco"
      : feminine
        ? "preta"
        : "preto";
  return `${base} ${colorName}`;
}

function describeCoachMove(move, boardAfter) {
  const piece = capitalizeFirst(pieceWithColor(move.piece, move.color));
  const captured = move.captured
    ? pieceWithColor(move.captured, move.color === "w" ? "b" : "w")
    : null;

  let text;
  if (move.flags.includes("k")) {
    text = `${piece} de ${move.from} para ${move.to} (roque curto)`;
  } else if (move.flags.includes("q")) {
    text = `${piece} de ${move.from} para ${move.to} (roque longo)`;
  } else if (move.promotion) {
    const promoted = PIECE_NAMES[move.promotion] ?? "dama";
    text = captured
      ? `${piece} de ${move.from} captura ${captured} em ${move.to} e é promovido a ${promoted}`
      : `${piece} de ${move.from} para ${move.to} e é promovido a ${promoted}`;
  } else if (captured) {
    text = `${piece} de ${move.from} captura ${captured} em ${move.to}`;
  } else {
    text = `${piece} de ${move.from} para ${move.to}`;
  }

  if (move.flags.includes("e")) text += " (captura en passant)";
  if (boardAfter.isCheckmate()) text += " e dá xeque-mate";
  else if (boardAfter.inCheck()) text += " e dá xeque";
  return text;
}

function buildCoachLine(startFen, ucis) {
  const line = { startFen, moves: [], fens: [startFen] };
  let chess;
  try {
    chess = new Chess(startFen);
  } catch {
    return line;
  }

  const moves = Array.isArray(ucis) ? ucis.slice(0, COACH_MAX_PLIES) : [];
  for (const uci of moves) {
    if (typeof uci !== "string" || uci.length < 4) continue;

    const beforeFen = chess.fen();
    const number = Number.parseInt(beforeFen.split(" ")[5], 10) || 1;
    const color = chess.turn();
    let move;

    try {
      move = chess.move({
        from: uci.slice(0, 2),
        to: uci.slice(2, 4),
        promotion: uci.length > 4 ? uci[4] : undefined,
      });
    } catch {
      break;
    }
    if (!move) break;

    const fen = chess.fen();
    line.moves.push({
      uci: `${move.from}${move.to}${move.promotion ?? ""}`,
      san: move.san,
      from: move.from,
      to: move.to,
      color,
      number,
      fen,
      description: describeCoachMove(move, chess),
    });
    line.fens.push(fen);

    if (chess.isGameOver()) break;
  }

  return line;
}

function buildCoachLines(result) {
  const startFen = result.fen_before;
  const bestUcis =
    Array.isArray(result.pv) && result.pv.length > 0
      ? result.pv
      : result.best_move?.uci
        ? [result.best_move.uci]
        : [];

  const playedUcis = [];
  if (result.user_move?.uci) playedUcis.push(result.user_move.uci);
  if (Array.isArray(result.refutation_pv)) {
    playedUcis.push(...result.refutation_pv);
  }

  return {
    best: buildCoachLine(startFen, bestUcis),
    played: buildCoachLine(startFen, playedUcis),
  };
}

function currentCoachLine() {
  return coach.lines[coach.active] ?? null;
}

function ensureCoachGround() {
  if (coach.ground) return;
  const line = currentCoachLine();
  if (!line) return;

  coach.ground = Chessground(ui.coachBoard, {
    fen: line.fens[0],
    orientation: state.orientation,
    viewOnly: true,
    coordinates: false,
    animation: { enabled: true, duration: 220 },
    highlight: { lastMove: true, check: true },
    drawable: { enabled: false, visible: true },
  });
}

function showCoachBoard(result) {
  const lines = buildCoachLines(result);
  if (lines.best.moves.length === 0 && lines.played.moves.length === 0) {
    hideCoachBoard();
    return;
  }

  stopCoachPlayback();
  coach.lines = lines;
  coach.active = lines.played.moves.length > 0 ? "played" : "best";
  coach.step = 0;
  coach.playing = false;

  ui.coachPanel.hidden = false;
  ui.appLayout.classList.add("coach-open");

  ensureCoachGround();
  updateCoachTabs();
  renderCoachMoves();
  renderCoach();
}

function hideCoachBoard() {
  stopCoachPlayback();
  coach.lines = { played: null, best: null };
  coach.active = "played";
  coach.step = 0;
  coach.playing = false;

  ui.coachPanel.hidden = true;
  ui.appLayout.classList.remove("coach-open");
  ui.coachMoves.replaceChildren();
  ui.coachCaption.textContent = "Posição inicial da sequência.";
}

function setCoachTab(tab) {
  const line = coach.lines[tab];
  if (!line || line.moves.length === 0) return;

  stopCoachPlayback();
  coach.active = tab;
  coach.step = 0;
  updateCoachTabs();
  renderCoachMoves();
  renderCoach();
}

function updateCoachTabs() {
  const playedAvailable = Boolean(coach.lines.played?.moves.length);
  const bestAvailable = Boolean(coach.lines.best?.moves.length);

  ui.coachTabPlayed.disabled = !playedAvailable;
  ui.coachTabBest.disabled = !bestAvailable;
  ui.coachTabPlayed.setAttribute(
    "aria-selected",
    String(coach.active === "played"),
  );
  ui.coachTabBest.setAttribute("aria-selected", String(coach.active === "best"));
}

function renderCoachMoves() {
  ui.coachMoves.replaceChildren();
  const line = currentCoachLine();
  if (!line) return;

  line.moves.forEach((move, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "coach-move";
    button.textContent = `${move.number}${move.color === "w" ? "." : "..."} ${move.san}`;
    button.title = move.description;
    button.setAttribute("aria-current", String(index + 1 === coach.step));
    button.addEventListener("click", () => {
      stopCoachPlayback();
      coach.step = index + 1;
      renderCoach();
    });
    ui.coachMoves.append(button);
  });
}

function renderCoach() {
  const line = currentCoachLine();
  if (!line || !coach.ground) return;

  coach.step = Math.max(0, Math.min(line.moves.length, coach.step));
  const fen = line.fens[coach.step];
  const lastMove = coach.step > 0 ? line.moves[coach.step - 1] : null;

  let inCheck = false;
  try {
    inCheck = new Chess(fen).inCheck();
  } catch {
    inCheck = false;
  }

  const turnColor = fen.split(" ")[1] === "b" ? "black" : "white";
  coach.ground.set({
    fen,
    orientation: state.orientation,
    turnColor,
    check: inCheck ? turnColor : false,
    lastMove: lastMove ? [lastMove.from, lastMove.to] : undefined,
  });

  if (lastMove) {
    const brush =
      coach.active === "best" ? "green" : coach.step === 1 ? "red" : "blue";
    coach.ground.setShapes([
      { orig: lastMove.from, dest: lastMove.to, brush },
    ]);
  } else {
    coach.ground.setShapes([]);
  }

  if (lastMove) {
    let prefix;
    if (coach.step === 1) {
      prefix = coach.active === "played" ? "Seu lance" : "Melhor lance";
    } else if (coach.active === "played" && coach.step === 2) {
      prefix = "Resposta do adversário";
    } else {
      prefix =
        coach.active === "played" ? "Continuação possível" : "Continuação";
    }
    ui.coachCaption.textContent = `${prefix}: ${lastMove.description}.`;
  } else if (coach.active === "played") {
    ui.coachCaption.textContent =
      "Posição antes do seu lance. Use ▶ para ver o que o adversário pode fazer.";
  } else {
    ui.coachCaption.textContent =
      "Posição antes do lance. Use ▶ para ver a linha que o Stockfish preferia.";
  }

  ui.coachStep.textContent = `${coach.step} / ${line.moves.length}`;
  ui.coachStart.disabled = coach.step === 0;
  ui.coachPrev.disabled = coach.step === 0;
  ui.coachNext.disabled = coach.step >= line.moves.length;
  ui.coachPlay.disabled = line.moves.length === 0;
  ui.coachPlay.textContent = coach.playing ? "⏸" : "▶";
  ui.coachPlay.setAttribute(
    "aria-label",
    coach.playing ? "Pausar sequência" : "Reproduzir sequência",
  );
  ui.coachPlay.title = coach.playing ? "Pausar" : "Reproduzir";

  [...ui.coachMoves.children].forEach((button, index) => {
    button.setAttribute("aria-current", String(index + 1 === coach.step));
  });
}

function stopCoachPlayback() {
  if (coach.timer !== null) {
    window.clearInterval(coach.timer);
    coach.timer = null;
  }
  coach.playing = false;
}

function toggleCoachPlayback() {
  const line = currentCoachLine();
  if (!line || line.moves.length === 0) return;

  if (coach.playing) {
    stopCoachPlayback();
    renderCoach();
    return;
  }

  if (coach.step >= line.moves.length) coach.step = 0;
  coach.playing = true;
  renderCoach();

  coach.timer = window.setInterval(() => {
    const activeLine = currentCoachLine();
    if (!activeLine || coach.step >= activeLine.moves.length) {
      stopCoachPlayback();
      renderCoach();
      return;
    }
    coach.step += 1;
    renderCoach();
  }, 1200);
}

function rollbackMove(fenAfterUser, message) {
  if (state.game.fen() === fenAfterUser) {
    state.game.undo();
  }

  state.boardLocked = false;
  setAnalyzing(false);
  hideQualityBadge();
  clearArrows();
  renderBoard(lastMoveFromHistory());
  renderMoves();
  updateEvalForCurrentFen();
  showEngineWarning(`Não foi possível analisar o lance: ${message}`);
  appendChatNote(`⚠️ Análise indisponível: ${message}`);
}

function startNewGame() {
  state.moveController?.abort();
  const previousGameId = state.gameId;
  state.gameEpoch += 1;
  state.gameId = null;
  state.gamePromise = null;
  state.game = new Chess();
  state.boardLocked = false;
  state.analyzing = false;
  state.lastAnalysis = null;
  state.evalByFen = new Map([[fenKey(START_FEN), NEUTRAL_EVAL]]);
  setAnalyzing(false);
  clearArrows();
  hideQualityBadge();
  hideCoachBoard();
  renderBoard();
  renderMoves();
  updateEval(NEUTRAL_EVAL);
  if (previousGameId) {
    void endGameRecord(previousGameId, "new_game");
  }
  void ensureGameRecord().catch((error) => {
    console.warn("Não foi possível iniciar o histórico do novo jogo:", error);
    appendChatNote(
      "⚠️ O novo jogo não pôde ser salvo no histórico SQLite.",
    );
  });
  appendChatNote("Novo jogo iniciado.");
}

function flipBoard() {
  state.orientation = state.orientation === "white" ? "black" : "white";
  ground.set({ orientation: state.orientation });
  renderBoardCoords();
  if (coach.ground) {
    coach.ground.set({ orientation: state.orientation });
    renderCoach();
  }
  ui.evalBar.classList.toggle("flipped", state.orientation === "black");
  void recordGameEvent("flip_board", { orientation: state.orientation });
}

function undoMove() {
  if (state.game.history().length === 0) return;

  state.moveController?.abort();
  state.moveController = null;
  state.boardLocked = false;
  setAnalyzing(false);
  clearArrows();
  hideQualityBadge();
  hideCoachBoard();

  let undone = 0;
  do {
    state.game.undo();
    undone += 1;
    if (state.game.turn() === "w") break;
  } while (undone < 2 && state.game.history().length > 0);

  if (state.gameId) {
    void recordUndo(state.gameId, undone);
  }

  renderBoard(lastMoveFromHistory());
  renderMoves();
  updateEvalForCurrentFen();
  appendChatNote("Lance desfeito.");
}

function analysisContext(result) {
  return {
    fen_before: result.fen_before ?? null,
    played: result.user_move?.san ?? null,
    played_uci: result.user_move?.uci ?? null,
    best_move: result.best_move?.san ?? null,
    best_move_uci: result.best_move?.uci ?? null,
    pv: Array.isArray(result.pv_san) ? result.pv_san : null,
    refutation_pv: Array.isArray(result.refutation_pv_san)
      ? result.refutation_pv_san
      : null,
    cp_loss: Number.isFinite(result.cp_loss) ? result.cp_loss : null,
    classification: result.classification ?? null,
    eval_before: result.evaluation?.before?.label ?? null,
    eval_after: result.evaluation?.after_user?.label ?? null,
    bot_move: result.bot_move?.san ?? null,
  };
}

function buildChatPayload(message, analysis = null) {
  return {
    message,
    fen: state.game.fen(),
    pgn: state.game.pgn(),
    student_level: state.trainerLevel,
    analysis,
  };
}

function trainerLevelLabel() {
  return {
    beginner: "iniciante",
    intermediate: "intermediário",
    advanced: "avançado",
  }[state.trainerLevel] ?? "iniciante";
}

function enqueueAutoExplanation(result) {
  const best = result.best_move?.san ?? result.best_move?.uci ?? "o melhor lance do Stockfish";
  const message =
    `Meu lance ${result.user_move?.san ?? result.user_move?.uci} foi classificado como ` +
    `${result.classification}. Quero entender o conceito tático ou estratégico por trás ` +
    `do melhor lance ${best} e por que o meu lance foi pior.`;

  appendChatNote(
    `Stockfish: ${result.classification} · explicação para ${trainerLevelLabel()}`,
  );
  state.chatQueue.push(buildChatPayload(message, analysisContext(result)));
  pumpChatQueue();
}

function submitChat() {
  const message = ui.chatInput.value.trim();
  if (!message || state.chatBusy) return;

  appendUserMessage(message);
  ui.chatInput.value = "";
  state.chatQueue.push(buildChatPayload(message));
  updateChatControls();
  pumpChatQueue();
}

async function checkHealth() {
  try {
    const response = await fetch("/health");
    if (!response.ok) return;
    const health = await response.json();

    state.agyAvailable = Boolean(health.agy?.found);
    updateChatControls();

    if (!health.stockfish?.uci) {
      const suffix = health.stockfish?.found
        ? "o executável não respondeu ao comando uci"
        : "não encontrado";
      showEngineWarning(
        `Stockfish ${suffix}. Instale o Stockfish conforme o README e reinicie o servidor.`,
      );
    } else {
      clearEngineWarning();
    }

    if (!health.agy?.found) {
      appendChatNote(
        "⚠️ agy não encontrado neste host. O chat só funciona onde o CLI estiver instalado e autenticado localmente.",
      );
    }
  } catch (error) {
    console.warn("Não foi possível consultar /health:", error);
  }
}

function updateChatControls() {
  ui.chatSend.disabled = state.chatBusy || ui.chatInput.value.trim().length === 0;

  if (state.chatBusy) {
    if (state.chatQueue.length > 0) {
      ui.chatStatus.dataset.state = "queued";
      ui.chatStatus.textContent = `${state.chatQueue.length} na fila`;
    } else {
      ui.chatStatus.dataset.state = "thinking";
      ui.chatStatus.textContent = "pensando";
    }
    return;
  }

  if (state.agyAvailable === false) {
    ui.chatStatus.dataset.state = "offline";
    ui.chatStatus.textContent = "agy ausente";
    return;
  }

  ui.chatStatus.dataset.state = "ready";
  ui.chatStatus.textContent = "pronto";
}

function pumpChatQueue() {
  updateChatControls();
  if (state.chatBusy) return;
  const payload = state.chatQueue.shift();
  if (!payload) {
    updateChatControls();
    return;
  }
  void streamTrainerReply(payload);
}

async function streamTrainerReply(payload) {
  state.chatBusy = true;
  updateChatControls();

  const bubble = createChatBubble("trainer", { thinking: true });
  const textElement = bubble.querySelector(".bubble-text");

  try {
    const response = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      throw new Error(await readHttpError(response));
    }
    if (!response.body) {
      throw new Error("Este navegador não oferece suporte a respostas em streaming.");
    }

    await consumeSSE(response.body, bubble, textElement);

    if (!textElement.textContent.trim()) {
      textElement.textContent = "O treinador não retornou texto.";
    }
  } catch (error) {
    appendBubbleError(bubble, error.message);
  } finally {
    bubble.classList.remove("is-thinking");
    state.chatBusy = false;
    updateChatControls();
    scrollChatToBottom();

    if (state.chatQueue.length > 0) {
      queueMicrotask(pumpChatQueue);
    }
  }
}

async function consumeSSE(stream, bubble, textElement, onUpdate = scrollChatToBottom) {
  const reader = stream.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let sawDelta = false;
  let sawDone = false;
  let sawError = false;

  const processFrame = (rawFrame) => {
    const dataText = rawFrame
      .split(/\r?\n/)
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).replace(/^ /, ""))
      .join("\n");

    if (!dataText) return;

    let data;
    try {
      data = JSON.parse(dataText);
    } catch {
      return;
    }

    if (typeof data.delta === "string" && data.delta.length > 0) {
      if (!sawDelta) {
        sawDelta = true;
        bubble.classList.remove("is-thinking");
      }
      textElement.textContent += data.delta;
      onUpdate();
    }

    if (typeof data.error === "string" && data.error.length > 0) {
      sawError = true;
      appendBubbleError(bubble, data.error, onUpdate);
    }

    if (data.done === true) {
      sawDone = true;
    }
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    let match = buffer.match(/\r?\n\r?\n/);
    while (match) {
      const boundary = match.index ?? 0;
      const rawFrame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + match[0].length);
      processFrame(rawFrame);

      if (sawDone) {
        try {
          await reader.cancel();
        } catch {
          // O servidor já encerrou o stream.
        }
        return;
      }
      match = buffer.match(/\r?\n\r?\n/);
    }
  }

  buffer += decoder.decode();
  if (buffer.trim()) processFrame(buffer);

  if (!sawDone && !sawError) {
    appendBubbleError(
      bubble,
      "A conexão com o treinador terminou antes do sinal de conclusão.",
      onUpdate,
    );
  }
}

function createChatBubble(role, { thinking = false } = {}) {
  const bubble = document.createElement("article");
  bubble.className = `chat-bubble ${role}`;
  if (thinking) bubble.classList.add("is-thinking");

  const meta = document.createElement("div");
  meta.className = "bubble-meta";
  meta.textContent = role === "user" ? "Você" : "Treinador";

  const text = document.createElement("div");
  text.className = "bubble-text";

  bubble.append(meta);

  if (role === "trainer") {
    const thinkingLine = document.createElement("div");
    thinkingLine.className = "thinking";
    const dots = document.createElement("span");
    dots.className = "thinking-dots";
    dots.setAttribute("aria-hidden", "true");
    dots.append(
      document.createElement("span"),
      document.createElement("span"),
      document.createElement("span"),
    );
    thinkingLine.append(dots, document.createTextNode(" pensando…"));
    bubble.append(thinkingLine);
  }

  bubble.append(text);
  ui.chatMessages.append(bubble);
  scrollChatToBottom();
  return bubble;
}

function appendUserMessage(message) {
  const bubble = createChatBubble("user");
  bubble.querySelector(".bubble-text").textContent = message;
}

function appendChatNote(message) {
  const note = document.createElement("div");
  note.className = "chat-note";
  note.textContent = message;
  ui.chatMessages.append(note);
  scrollChatToBottom();
}

function appendBubbleError(bubble, message, onUpdate = scrollChatToBottom) {
  const text = bubble.querySelector(".bubble-text");
  const prefix = text.textContent.trim().length > 0 ? "\n\n" : "";
  text.textContent += `${prefix}⚠️ ${message}`;
  bubble.classList.add("error");
  bubble.classList.remove("is-thinking");
  onUpdate();
}

function scrollChatToBottom() {
  if (scrollFrame !== null) return;
  scrollFrame = window.requestAnimationFrame(() => {
    scrollFrame = null;
    ui.chatMessages.scrollTop = ui.chatMessages.scrollHeight;
  });
}

function scrollReviewToBottom() {
  if (reviewScrollFrame !== null) return;
  reviewScrollFrame = window.requestAnimationFrame(() => {
    reviewScrollFrame = null;
    ui.reviewAiMessages.scrollTop = ui.reviewAiMessages.scrollHeight;
  });
}

function formatReviewDate(iso) {
  if (!iso) return "data desconhecida";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso.slice(0, 16).replace("T", " ");
  }
  return date.toLocaleString("pt-BR", {
    dateStyle: "short",
    timeStyle: "short",
  });
}

function reviewResultLabel(game) {
  if (game.result === "1-0") return "1–0";
  if (game.result === "0-1") return "0–1";
  if (game.result === "1/2-1/2") return "empate";
  return game.game_over ? "finalizada" : "em andamento";
}

function makeReviewStat(label, value, note) {
  const card = document.createElement("div");
  card.className = "review-stat";

  const labelElement = document.createElement("div");
  labelElement.className = "review-stat-label";
  labelElement.textContent = label;

  const valueElement = document.createElement("div");
  valueElement.className = "review-stat-value";
  valueElement.textContent = value;

  const noteElement = document.createElement("div");
  noteElement.className = "review-stat-note";
  noteElement.textContent = note;

  card.append(labelElement, valueElement, noteElement);
  return card;
}

function renderReviewStats(stats) {
  ui.reviewStats.replaceChildren();

  const trend = stats.trend || {};
  const classifications = stats.classifications || {};
  const recent = trend.recent_accuracy;
  const previous = trend.previous_accuracy;
  const trendNote =
    recent === null || recent === undefined
      ? "sem dados suficientes"
      : previous === null || previous === undefined
        ? `precisão recente ${recent}%`
        : `recente ${recent}% · anterior ${previous}%`;

  ui.reviewStats.append(
    makeReviewStat(
      "Partidas",
      String(stats.games ?? 0),
      "com lances registrados",
    ),
    makeReviewStat(
      "Lances analisados",
      String(stats.analyzed_user_moves ?? 0),
      `${stats.bad_moves ?? 0} ruins`,
    ),
    makeReviewStat(
      "Precisão estimada",
      stats.accuracy === null || stats.accuracy === undefined
        ? "—"
        : `${stats.accuracy}%`,
      trendNote,
    ),
    makeReviewStat(
      "Perda média",
      stats.avg_cp_loss === null || stats.avg_cp_loss === undefined
        ? "—"
        : `${stats.avg_cp_loss} cp`,
      `${stats.bad_rate ?? "—"}% de lances ruins`,
    ),
    makeReviewStat(
      "Erros + gafes",
      String(stats.blunders ?? 0),
      `Excelente: ${classifications.Excelente ?? 0} · Bom: ${classifications.Bom ?? 0}`,
    ),
  );

  const phaseItems = Object.entries(stats.phases || {})
    .filter(([, item]) => item.total > 0 && item.bad_rate !== null)
    .sort(([, a], [, b]) => b.bad_rate - a.bad_rate);
  const pieceItems = Object.entries(stats.pieces || {})
    .filter(([, item]) => item.total > 0 && item.bad_rate !== null)
    .sort(([, a], [, b]) => b.bad_rate - a.bad_rate);
  const opening = (stats.openings || [])[0];

  const insights = document.createElement("div");
  insights.className = "review-stat review-stat-wide";

  const insightTitle = document.createElement("div");
  insightTitle.className = "review-stat-label";
  insightTitle.textContent = "Padrões do histórico";
  insights.append(insightTitle);

  const insightText = document.createElement("div");
  insightText.className = "review-insights-text";
  insightText.textContent = [
    phaseItems.length
      ? `Fase mais frágil: ${phaseItems[0][0]} (${phaseItems[0][1].bad_rate}% de lances ruins)`
      : "Fase mais frágil: sem dados",
    pieceItems.length
      ? `Peça que mais erra: ${pieceItems[0][0]} (${pieceItems[0][1].bad_rate}%)`
      : "Peça que mais erra: sem dados",
    opening
      ? `Sequência inicial mais repetida: ${opening.line} (${opening.count}x)`
      : "Sequência inicial: sem dados",
  ].join(" · ");
  insights.append(insightText);
  ui.reviewStats.append(insights);
}

function renderReviewGames(games) {
  ui.reviewGames.replaceChildren();
  ui.reviewGamesCount.textContent = String(games.length);
  ui.reviewCount.textContent = String(games.length);
  ui.reviewCount.hidden = games.length === 0;

  if (games.length === 0) {
    const empty = document.createElement("p");
    empty.className = "review-ai-hint";
    empty.textContent = "Nenhuma partida com lances ainda.";
    ui.reviewGames.append(empty);
    return;
  }

  for (const game of games) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "review-game-button";
    button.dataset.gameId = String(game.id);
    button.setAttribute(
      "aria-current",
      String(game.id === state.reviewGameId),
    );

    const top = document.createElement("div");
    top.className = "review-game-top";
    const title = document.createElement("span");
    title.textContent = `Partida #${game.id}`;
    const result = document.createElement("span");
    result.textContent = reviewResultLabel(game);
    top.append(title, result);

    const meta = document.createElement("div");
    meta.className = "review-game-meta";
    meta.textContent =
      `${formatReviewDate(game.started_at)} · nível ${game.level} · ` +
      `${game.analyzed_user_moves} lances analisados · ` +
      `perda média ${game.avg_cp_loss ?? "—"} cp · ` +
      `precisão ${game.accuracy ?? "—"}%`;

    const tags = document.createElement("div");
    tags.className = "review-game-tags";
    for (const label of ["Imprecisão", "Erro", "Gafe"]) {
      const count = game.classifications?.[label] ?? 0;
      if (count > 0) {
        const tag = document.createElement("span");
        tag.className = "review-tag";
        tag.textContent = `${count} ${label.toLowerCase()}${count > 1 ? "s" : ""}`;
        tags.append(tag);
      }
    }
    if (game.undone_plies > 0) {
      const tag = document.createElement("span");
      tag.className = "review-tag";
      tag.textContent = `${game.undone_plies} desfeito${game.undone_plies > 1 ? "s" : ""}`;
      tags.append(tag);
    }

    button.append(top, meta);
    if (tags.children.length) button.append(tags);
    button.addEventListener("click", () => {
      if (!state.reviewBusy) void selectReviewGame(game.id);
    });
    ui.reviewGames.append(button);
  }
}

function updateReviewSelection() {
  [...ui.reviewGames.querySelectorAll(".review-game-button")].forEach((button) => {
    button.setAttribute(
      "aria-current",
      String(Number(button.dataset.gameId) === state.reviewGameId),
    );
  });
}

function renderReviewGame(data) {
  const game = data.game;
  const summary = data.summary || {};
  state.reviewGameId = game.id;

  ui.reviewEmpty.hidden = true;
  ui.reviewContent.hidden = false;
  ui.reviewDetailTitle.textContent = `Partida #${game.id}`;
  ui.reviewGameSubtitle.textContent =
    `${formatReviewDate(game.started_at)} · nível ${game.level} · ` +
    `resultado ${reviewResultLabel(game)}`;

  ui.reviewGameSummary.replaceChildren();
  const counts = summary.classifications || {};
  for (const label of ["Excelente", "Bom", "Imprecisão", "Erro", "Gafe"]) {
    const count = counts[label] ?? 0;
    if (count === 0) continue;
    const badge = document.createElement("span");
    badge.className = "quality-badge";
    badge.dataset.quality = QUALITY_SLUGS[label] ?? "";
    badge.textContent = `${label}: ${count}`;
    ui.reviewGameSummary.append(badge);
  }
  const metrics = [
    `Lances: ${summary.plies ?? 0}`,
    `Perda média: ${summary.avg_cp_loss ?? "—"} cp`,
    `Precisão: ${summary.accuracy ?? "—"}%`,
  ];
  for (const text of metrics) {
    const tag = document.createElement("span");
    tag.className = "review-tag";
    tag.textContent = text;
    ui.reviewGameSummary.append(tag);
  }

  ui.reviewMoves.replaceChildren();
  for (const move of data.moves || []) {
    const row = document.createElement("div");
    row.className = "review-move";
    const isBad = BAD_CLASSIFICATIONS.has(move.classification);
    if (isBad) row.classList.add("is-bad");

    const number = document.createElement("span");
    number.className = "move-no";
    number.textContent = `${move.move_number}${move.color === "w" ? "." : "..."}`;

    const actor = document.createElement("span");
    actor.className = "move-actor";
    actor.textContent = move.actor === "user" ? "Você" : "Bot";

    const san = document.createElement("span");
    san.className = "move-san";
    san.textContent = move.san;

    const detail = document.createElement("span");
    detail.className = "move-detail";
    if (move.classification) {
      const badge = document.createElement("span");
      badge.className = "quality-badge compact";
      badge.dataset.quality = QUALITY_SLUGS[move.classification] ?? "";
      badge.textContent = move.classification;
      detail.append(badge);
    }
    if (move.cp_loss !== null && move.cp_loss !== undefined) {
      const loss = document.createElement("span");
      loss.textContent = `−${move.cp_loss} cp`;
      detail.append(loss);
    }
    if (move.eval_before || move.eval_after) {
      const evaluation = document.createElement("span");
      evaluation.className = "review-eval";
      evaluation.textContent = `${move.eval_before ?? "—"} → ${move.eval_after ?? "—"}`;
      detail.append(evaluation);
    }
    if (move.actor === "user" && isBad && move.best_move_san) {
      const best = document.createElement("span");
      best.textContent = `melhor: ${move.best_move_san}`;
      detail.append(best);
    }

    row.append(number, actor, san, detail);
    ui.reviewMoves.append(row);
  }

  const undone = data.undone_moves || [];
  ui.reviewUndoneWrap.hidden = undone.length === 0;
  ui.reviewUndone.replaceChildren();
  if (undone.length > 0) {
    const list = document.createElement("div");
    list.className = "review-undone-list";
    for (const move of undone) {
      const item = document.createElement("span");
      item.textContent =
        `${move.move_number}${move.color === "w" ? "." : "..."} ${move.san} ` +
        `[${move.actor === "user" ? "você" : "bot"}] · ` +
        `${move.classification ?? "sem classificação"}`;
      list.append(item);
    }
    const summaryElement = ui.reviewUndoneWrap.querySelector("summary");
    summaryElement.textContent = `Lances desfeitos nesta partida (${undone.length})`;
    ui.reviewUndone.append(list);
  }

  updateReviewSelection();
  updateReviewControls();
}

async function selectReviewGame(gameId) {
  if (state.reviewBusy) return;
  try {
    const response = await fetch(`/games/${gameId}`);
    if (!response.ok) {
      throw new Error(await readHttpError(response));
    }
    const data = await response.json();
    renderReviewGame(data);
  } catch (error) {
    ui.reviewEmpty.hidden = false;
    ui.reviewEmpty.textContent = `Não foi possível abrir a partida: ${error.message}`;
    ui.reviewContent.hidden = true;
  }
}

async function loadReview() {
  if (state.reviewLoading) return;
  state.reviewLoading = true;
  updateReviewControls();

  try {
    const [statsResponse, gamesResponse] = await Promise.all([
      fetch("/review/stats"),
      fetch("/games?limit=50"),
    ]);
    if (!statsResponse.ok) {
      throw new Error(await readHttpError(statsResponse));
    }
    if (!gamesResponse.ok) {
      throw new Error(await readHttpError(gamesResponse));
    }

    const stats = await statsResponse.json();
    const gamesPayload = await gamesResponse.json();
    const games = gamesPayload.games || [];
    state.reviewStats = stats;
    renderReviewStats(stats);
    renderReviewGames(games);
    ui.reviewEmpty.hidden = games.length > 0;
    ui.reviewContent.hidden = games.length === 0;
    ui.reviewGameFeedback.disabled = games.length === 0;

    if (games.length === 0) {
      ui.reviewEmpty.textContent =
        "Jogue algumas partidas no modo Treino. Cada lance analisado aparece aqui.";
      state.reviewGameId = null;
      updateReviewControls();
      return;
    }

    const selectedId = games.some((game) => game.id === state.reviewGameId)
      ? state.reviewGameId
      : games[0].id;
    await selectReviewGame(selectedId);
  } catch (error) {
    ui.reviewEmpty.hidden = false;
    ui.reviewEmpty.textContent = `Não foi possível carregar o revisor: ${error.message}`;
    ui.reviewContent.hidden = true;
    ui.reviewGames.replaceChildren();
    ui.reviewStats.replaceChildren();
    ui.reviewCount.hidden = true;
  } finally {
    state.reviewLoading = false;
    updateReviewControls();
  }
}

function setView(view) {
  const review = view === "review";
  ui.trainerView.hidden = review;
  ui.reviewView.hidden = !review;
  ui.viewTrainer.setAttribute("aria-selected", String(!review));
  ui.viewReview.setAttribute("aria-selected", String(review));

  if (review) {
    window.scrollTo({ top: 0, behavior: "smooth" });
    if (!state.reviewBusy) void loadReview();
  }
}

function updateReviewControls() {
  const busy = state.reviewLoading || state.reviewBusy;
  const hasOverallData = (state.reviewStats?.analyzed_user_moves ?? 0) > 0;
  ui.reviewGameFeedback.disabled = busy || !state.reviewGameId;
  ui.reviewOverall.disabled = busy || !hasOverallData;
  ui.reviewRefresh.disabled = busy;
}

function createReviewBubble(label) {
  const bubble = document.createElement("article");
  bubble.className = "chat-bubble trainer is-thinking";

  const meta = document.createElement("div");
  meta.className = "bubble-meta";
  meta.textContent = label;

  const thinking = document.createElement("div");
  thinking.className = "thinking";
  const dots = document.createElement("span");
  dots.className = "thinking-dots";
  dots.setAttribute("aria-hidden", "true");
  dots.append(
    document.createElement("span"),
    document.createElement("span"),
    document.createElement("span"),
  );
  thinking.append(dots, document.createTextNode(" pensando…"));

  const text = document.createElement("div");
  text.className = "bubble-text";

  bubble.append(meta, thinking, text);
  ui.reviewAiMessages.querySelector(".review-ai-hint")?.remove();
  ui.reviewAiMessages.append(bubble);
  scrollReviewToBottom();
  return bubble;
}

async function streamReviewFeedback(url, payload, label) {
  if (state.reviewBusy) return;
  state.reviewBusy = true;
  updateReviewControls();
  ui.reviewAiStatus.dataset.state = "thinking";
  ui.reviewAiStatus.textContent = "pensando";

  const bubble = createReviewBubble(`Revisor · ${label}`);
  const textElement = bubble.querySelector(".bubble-text");
  ui.reviewAiMessages.scrollIntoView({ block: "nearest", behavior: "smooth" });

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      throw new Error(await readHttpError(response));
    }
    if (!response.body) {
      throw new Error("Este navegador não oferece suporte a streaming.");
    }

    await consumeSSE(response.body, bubble, textElement, scrollReviewToBottom);
    if (!textElement.textContent.trim()) {
      textElement.textContent = "O revisor não retornou texto.";
    }
  } catch (error) {
    appendBubbleError(bubble, error.message, scrollReviewToBottom);
  } finally {
    bubble.classList.remove("is-thinking");
    state.reviewBusy = false;
    ui.reviewAiStatus.dataset.state = "ready";
    ui.reviewAiStatus.textContent = "pronto";
    updateReviewControls();
    scrollReviewToBottom();
  }
}

init();
