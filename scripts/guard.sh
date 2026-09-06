#!/usr/bin/env bash
# Hook pra-eksekusi untuk perintah shell. Exit 2 + stderr = blokir. Fail-closed:
# perintah yang tidak bisa dibaca ikut diblokir, bukan diloloskan.
source "$(dirname "$0")/_json.sh"
INPUT="$(cat)"
CMD="$(json_get "$INPUT" "tool_input.command")"
[ -z "$CMD" ] && { echo "guard.sh: tidak bisa membaca perintah; diblokir." >&2; exit 2; }
deny() { echo "DIBLOKIR guard.sh: $1. Perintah: $CMD" >&2; exit 2; }
echo "$CMD" | grep -Eq 'git +push'                          && deny "push dilakukan manusia"
echo "$CMD" | grep -Eq 'rm +-rf +(/|~|\.\.|\*)'             && deny "rm -rf berbahaya"
echo "$CMD" | grep -Eq 'dangerously|bypassPermissions'      && deny "jangan naikkan izin sendiri"
echo "$CMD" | grep -Eq '(_API_KEY|API_KEY=|MCP_KEY)' && echo "$CMD" | grep -Eq '(^|[^a-z])(echo|cat|printf|env|printenv|set)([^a-z]|$)' && deny "jangan cetak kredensial"
echo "$CMD" | grep -Eq 'cat +\.env([^.]|$)'                  && deny ".env tidak pernah dibaca otomatis"
echo "$CMD" | grep -Eiq '(solana +transfer|spl-token +transfer|sendTransaction|signAndSend)' && deny "tidak ada transaksi nyata (aturan hackathon)"
echo "$CMD" | grep -Eq 'sqlite3 .*(DELETE|UPDATE|DROP)'      && deny "store append-only; jangan ubah/hapus baris"
exit 0
