#!/bin/bash
# on_screen_keyboard.sh - с поддержкой языков

# Загрузить конфигурацию
CONFIG_FILE="$HOME/.config/Ax-Shell/config/wvkbd.conf"

# Значения по умолчанию
LAYERS="simple,cyrillic,emoji"
LANDSCAPE_LAYERS="simple,cyrillic,emoji"
HEIGHT=300

# Загрузить конфиг если существует
if [ -f "$CONFIG_FILE" ]; then
    source "$CONFIG_FILE"
fi

# Определить какая клавиатура доступна
detect_keyboard() {
    if command -v wvkbd-mobintl &> /dev/null; then
        echo "wvkbd-mobintl"
    elif command -v squeekboard &> /dev/null; then
        echo "squeekboard"
    elif command -v onboard &> /dev/null; then
        echo "onboard"
    elif command -v svkbd &> /dev/null; then
        echo "svkbd"
    else
        echo "none"
    fi
}

# Запустить клавиатуру
start_keyboard() {
    local kbd="$1"
    case "$kbd" in
        "wvkbd-mobintl")
            wvkbd-mobintl \
                -L "$HEIGHT" \
                -l "$LAYERS" \
                --landscape-layers "$LANDSCAPE_LAYERS" &
            ;;
        "squeekboard")
            squeekboard &
            ;;
        "onboard")
            onboard &
            ;;
        "svkbd")
            svkbd-mobile-intl &
            ;;
        *)
            notify-send "On-Screen Keyboard" \
                "No keyboard application found" -u critical
            return 1
            ;;
    esac
}

# Проверить запущена ли клавиатура
is_running() {
    local kbd="$1"
    case "$kbd" in
        "wvkbd-mobintl")
            pgrep -x "wvkbd-mobintl" > /dev/null
            ;;
        "squeekboard")
            pgrep -x "squeekboard" > /dev/null
            ;;
        "onboard")
            pgrep -x "onboard" > /dev/null
            ;;
        "svkbd")
            pgrep -x "svkbd" > /dev/null
            ;;
        *)
            return 1
            ;;
    esac
}

# Остановить клавиатуру
stop_keyboard() {
    local kbd="$1"
    case "$kbd" in
        "wvkbd-mobintl")
            pkill -x wvkbd-mobintl
            ;;
        "squeekboard")
            pkill -x squeekboard
            ;;
        "onboard")
            pkill -x onboard
            ;;
        "svkbd")
            pkill -x svkbd
            ;;
    esac
}

# Главная функция
main() {
    local action="${1:-toggle}"
    local kbd=$(detect_keyboard)

    case "$action" in
        "toggle")
            if [ "$kbd" = "none" ]; then
                notify-send "On-Screen Keyboard" "No keyboard application found" -u critical
                exit 1
            fi

            if is_running "$kbd"; then
                stop_keyboard "$kbd"
                notify-send "On-Screen Keyboard" "Keyboard hidden" -t 2000
            else
                start_keyboard "$kbd"
                notify-send "On-Screen Keyboard" "Keyboard shown" -t 2000
            fi
            ;;

        "show")
            if [ "$kbd" = "none" ]; then
                notify-send "On-Screen Keyboard" "No keyboard application found" -u critical
                exit 1
            fi

            if ! is_running "$kbd"; then
                start_keyboard "$kbd"
                notify-send "On-Screen Keyboard" "Keyboard shown" -t 2000
            fi
            ;;

        "hide")
            if [ "$kbd" != "none" ] && is_running "$kbd"; then
                stop_keyboard "$kbd"
                notify-send "On-Screen Keyboard" "Keyboard hidden" -t 2000
            fi
            ;;

        "check")
            # Для использования в checker
            kbd=$(detect_keyboard)
            if [ "$kbd" != "none" ] && is_running "$kbd"; then
                echo "t"
            else
                echo "f"
            fi
            ;;

        "status")
            if [ "$kbd" = "none" ]; then
                echo "No keyboard application found"
                echo "Install one of: wvkbd-mobintl, squeekboard, onboard, svkbd"
                exit 1
            fi

            if is_running "$kbd"; then
                echo "Keyboard: $kbd (running)"
            else
                echo "Keyboard: $kbd (not running)"
            fi
            ;;

        *)
            echo "Usage: $0 {toggle|show|hide|check|status}"
            echo "  toggle - Toggle keyboard visibility"
            echo "  show   - Show keyboard"
            echo "  hide   - Hide keyboard"
            echo "  check  - Check if running (for status checker)"
            echo "  status - Show current status"
            exit 1
            ;;
    esac
}

main "$@"
