# source: json_get "<json>" "<key path a.b>"  → nilai atau kosong. Tanpa jq.
json_get() {
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "$1" | python3 -c 'import sys,json
try:
  d=json.load(sys.stdin)
  for k in sys.argv[1].split("."): d=d.get(k,"") if isinstance(d,dict) else ""
  print(d if not isinstance(d,bool) else str(d).lower())
except Exception: print("")' "$2" 2>/dev/null
  else
    key="${2##*.}"; printf '%s' "$1" | grep -oE "\"$key\"[[:space:]]*:[[:space:]]*(\"([^\"\\\\]|\\\\.)*\"|true|false)" | head -1 | sed -E 's/^"[^"]*"[[:space:]]*:[[:space:]]*//; s/^"//; s/"$//'
  fi
}
