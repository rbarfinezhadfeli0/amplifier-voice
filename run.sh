#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SERVER_DIR="$ROOT/voice-server"
CLIENT_DIR="$ROOT/voice-client"
ENV_FILE="$SERVER_DIR/.env"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# PIDs for cleanup
SERVER_PID=""
CLIENT_PID=""

cleanup() {
    echo ""
    echo -e "${YELLOW}Shutting down...${NC}"
    [[ -n "$SERVER_PID" ]] && kill "$SERVER_PID" 2>/dev/null && echo "  Stopped server (PID $SERVER_PID)"
    [[ -n "$CLIENT_PID" ]] && kill "$CLIENT_PID" 2>/dev/null && echo "  Stopped client (PID $CLIENT_PID)"
    SERVER_PID=""
    CLIENT_PID=""
}
trap cleanup EXIT

# ---------- env setup ----------
load_env() {
    if [[ -f "$ENV_FILE" ]]; then
        set -a
        source "$ENV_FILE"
        set +a
    fi
}

check_keys() {
    local ok=true
    if [[ -z "${OPENAI_API_KEY:-}" || "$OPENAI_API_KEY" == "your_openai_api_key_here" ]]; then
        ok=false
    fi
    if [[ -z "${ANTHROPIC_API_KEY:-}" || "$ANTHROPIC_API_KEY" == "your_anthropic_api_key_here" ]]; then
        ok=false
    fi
    $ok
}

setup_keys() {
    load_env
    if check_keys; then
        echo -e "${GREEN}API keys loaded.${NC}"
        return
    fi

    echo -e "${YELLOW}API keys not configured. Enter them now:${NC}"
    echo ""

    if [[ -z "${OPENAI_API_KEY:-}" || "$OPENAI_API_KEY" == "your_openai_api_key_here" ]]; then
        read -rp "  OPENAI_API_KEY: " OPENAI_API_KEY
        export OPENAI_API_KEY
    fi
    if [[ -z "${ANTHROPIC_API_KEY:-}" || "$ANTHROPIC_API_KEY" == "your_anthropic_api_key_here" ]]; then
        read -rp "  ANTHROPIC_API_KEY: " ANTHROPIC_API_KEY
        export ANTHROPIC_API_KEY
    fi

    # Save to .env
    cat > "$ENV_FILE" <<EOF
OPENAI_API_KEY=$OPENAI_API_KEY
ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY
EOF
    echo -e "${GREEN}Keys saved to $ENV_FILE${NC}"
}

# ---------- actions ----------
install_backend() {
    echo -e "${CYAN}Installing backend...${NC}"
    cd "$SERVER_DIR"
    [[ -d .venv ]] || uv venv
    uv pip install -e ".[test]"
    echo -e "${GREEN}Backend installed.${NC}"
}

install_frontend() {
    echo -e "${CYAN}Installing frontend...${NC}"
    cd "$CLIENT_DIR"
    npm install
    echo -e "${GREEN}Frontend installed.${NC}"
}

start_server() {
    if [[ -n "$SERVER_PID" ]]; then
        echo -e "${YELLOW}Server already running (PID $SERVER_PID)${NC}"
        return
    fi
    setup_keys
    echo -e "${CYAN}Starting backend on :8080 ...${NC}"
    cd "$SERVER_DIR"
    source .venv/bin/activate
    OPENAI_API_KEY="$OPENAI_API_KEY" ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
        uv run voice-server &
    SERVER_PID=$!
    echo -e "${GREEN}Server started (PID $SERVER_PID)${NC}"
}

start_client() {
    if [[ -n "$CLIENT_PID" ]]; then
        echo -e "${YELLOW}Client already running (PID $CLIENT_PID)${NC}"
        return
    fi
    echo -e "${CYAN}Starting frontend on :5173 ...${NC}"
    cd "$CLIENT_DIR"
    npm run dev &
    CLIENT_PID=$!
    echo -e "${GREEN}Client started (PID $CLIENT_PID)${NC}"
}

start_both() {
    start_server
    sleep 2
    start_client
}

stop_all() {
    cleanup
}

run_tests() {
    echo -e "${CYAN}Running backend tests...${NC}"
    cd "$SERVER_DIR"
    source .venv/bin/activate
    uv run pytest tests/ -v
    echo ""
    echo -e "${CYAN}Running frontend typecheck...${NC}"
    cd "$CLIENT_DIR"
    npx tsc -b
    echo -e "${GREEN}All checks passed.${NC}"
}

show_status() {
    echo ""
    if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
        echo -e "  Backend:  ${GREEN}running${NC} (PID $SERVER_PID) → http://localhost:8080"
    else
        SERVER_PID=""
        echo -e "  Backend:  ${RED}stopped${NC}"
    fi
    if [[ -n "$CLIENT_PID" ]] && kill -0 "$CLIENT_PID" 2>/dev/null; then
        echo -e "  Frontend: ${GREEN}running${NC} (PID $CLIENT_PID) → http://localhost:5173"
    else
        CLIENT_PID=""
        echo -e "  Frontend: ${RED}stopped${NC}"
    fi
    echo ""
}

show_menu() {
    echo ""
    echo -e "${BOLD}═══════════════════════════════════════${NC}"
    echo -e "${BOLD}  Amplifier Voice Assistant${NC}"
    echo -e "${BOLD}═══════════════════════════════════════${NC}"
    show_status
    echo -e "  ${CYAN}1${NC}) Install backend + frontend"
    echo -e "  ${CYAN}2${NC}) Start both (server + client)"
    echo -e "  ${CYAN}3${NC}) Start server only"
    echo -e "  ${CYAN}4${NC}) Start client only"
    echo -e "  ${CYAN}5${NC}) Stop all"
    echo -e "  ${CYAN}6${NC}) Run tests"
    echo -e "  ${CYAN}7${NC}) Set API keys"
    echo -e "  ${CYAN}8${NC}) Show logs (server)"
    echo -e "  ${CYAN}q${NC}) Quit"
    echo ""
}

# ---------- main loop ----------
while true; do
    show_menu
    read -rp "  Choice: " choice
    echo ""
    case "$choice" in
        1) install_backend; install_frontend ;;
        2) start_both ;;
        3) start_server ;;
        4) start_client ;;
        5) stop_all ;;
        6) run_tests ;;
        7) setup_keys ;;
        8)
            if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
                echo -e "${YELLOW}Tailing server output (Ctrl+C to return to menu)...${NC}"
                wait "$SERVER_PID" 2>/dev/null || true
            else
                echo -e "${RED}Server not running.${NC}"
            fi
            ;;
        q|Q) break ;;
        *) echo -e "${RED}Invalid choice.${NC}" ;;
    esac
done
