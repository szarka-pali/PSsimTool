#!/usr/bin/env bash
# PreToolUse hook pre Bash.
#
# Druhá záchranná sieť za 'deny' pravidlami v settings.json. Deny pravidlá riešia
# vzory príkazov; tento hook rieši prípady, ktoré sa vzorom vyjadrujú ťažko
# (napr. "závisí od aktuálneho branchu" alebo "závisí od premennej prostredia").
#
# Kontrakt:
#   stdin  = JSON s .tool_input.command
#   exit 0 = povoliť
#   exit 2 = ZABLOKOVAŤ; text na stderr sa vráti Claudovi ako dôvod
#
# Toto NIE je bezpečnostná hranica proti zlomyseľnému aktérovi — je to poistka
# proti nešťastnej náhode. Skutočnú ochranu rieš oprávneniami a prostredím.

set -uo pipefail

payload=$(cat)
cmd=$(printf '%s' "$payload" | jq -r '.tool_input.command // empty')
[[ -z "$cmd" ]] && exit 0

blokuj() { echo "ZABLOKOVANÉ hookom: $1" >&2; exit 2; }

# 1) Deštruktívne git operácie na chránených branchoch
if [[ "$cmd" =~ git[[:space:]]+push ]]; then
  branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
  if [[ "$branch" == "main" || "$branch" == "master" || "$branch" == "develop" ]]; then
    blokuj "push priamo na chránený branch '$branch'. Vytvor feature branch a otvor PR."
  fi
fi

if [[ "$cmd" =~ --force([[:space:]]|$) ]] && [[ "$cmd" =~ git[[:space:]]+push ]]; then
  blokuj "force push. Ak je naozaj potrebný, urob ho ručne."
fi

# 2) Produkčné prostredie
if [[ "$cmd" =~ (prod|production) ]] && [[ "$cmd" =~ (kubectl|terraform|aws|gcloud|psql|mysql|helm) ]]; then
  blokuj "príkaz mieri na produkciu. Produkčné zmeny robí človek."
fi

# 3) Mazanie po veľkých plochách
if [[ "$cmd" =~ rm[[:space:]]+(-[a-zA-Z]*r[a-zA-Z]*[[:space:]]+)*(/|~|\$HOME|\.\.) ]]; then
  blokuj "rekurzívne mazanie mimo projektu."
fi

# 4) Sťahovanie a rovno spúšťanie
if [[ "$cmd" =~ (curl|wget).*\|[[:space:]]*(sh|bash|zsh) ]]; then
  blokuj "curl|bash. Stiahni skript, nechaj ma ho prečítať, potom ho spusti."
fi

# 5) Preskočenie kontrol kvality
if [[ "$cmd" =~ (--no-verify|--no-gpg-sign|SKIP=) ]]; then
  blokuj "obchádzanie git hookov. Oprav príčinu, nevypínaj kontrolu."
fi

# 6) Tajomstvá do histórie shellu
if [[ "$cmd" =~ (AWS_SECRET|PRIVATE_KEY|_TOKEN=|PASSWORD=) ]]; then
  blokuj "v príkaze je citlivá hodnota. Použi .env alebo správcu tajomstiev."
fi

# --- PSsimTool ------------------------------------------------------------

# 7) Zápis do OPC UA mimo lokálneho mock servera.
#    Zápis na reálny stroj môže rozbehnúť os. Testuj proti `pssim mock-server`.
if [[ "$cmd" =~ (--write|write-node) ]] && [[ "$cmd" =~ opc\.tcp:// ]]; then
  if [[ ! "$cmd" =~ opc\.tcp://(localhost|127\.0\.0\.1|\[::1\]) ]]; then
    blokuj "zápis do OPC UA na nelokálny endpoint. Zápis testuj len proti mock serveru."
  fi
fi

# 8) Vstupné CAD súbory sú nenahraditeľné — v repozitári nie sú a znovu ich
#    nevyrobíš. assets/cache/ naopak mazať môžeš, tá je generovaná.
if [[ "$cmd" =~ (rm|del|Remove-Item).*models/ ]]; then
  blokuj "mazanie v models/. Vstupné CAD súbory nie sú vo verzovaní — zmaž ich ručne, ak to naozaj chceš."
fi

exit 0
