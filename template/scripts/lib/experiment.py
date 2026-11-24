"""Cahier d'expériences du workspace façon laboratoire."""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

GARDE_FOU = (
    "Un seul changement par expérience ; mêmes conditions avant et après ; "
    "une seule mesure n'est pas une preuve — refaire si l'écart est faible."
)
AVERTISSEMENT_ECHANTILLON = "Échantillon trop petit pour conclure"


def slugify(text, max_length=50):
    normalized = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')
    lowered = normalized.lower()
    cleaned = re.sub(r'[^a-z0-9]+', '-', lowered).strip('-')
    truncated = cleaned[:max_length].rstrip('-')
    return truncated or 'experience'


def get_numeric_paths(data, prefix=""):
    paths = []
    if isinstance(data, dict):
        for k, v in sorted(data.items()):
            path = f"{prefix}.{k}" if prefix else k
            if path in ("project", "generated_at", "since"):
                continue
            if isinstance(v, dict):
                paths.extend(get_numeric_paths(v, path))
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                paths.append(path)
            elif v is None:
                paths.append(path)
    return paths


def resolve_metric(data, path):
    parts = path.split('.')
    current = data
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    if path in ("project", "generated_at", "since"):
        return False, None
    if current is None or (isinstance(current, (int, float)) and not isinstance(current, bool)):
        return True, current
    return False, None


def get_default_direction(metric):
    parts = metric.split('.')
    last = parts[-1]
    if (
        metric.startswith('tokens.')
        or last == 'tokens'
        or last in ('mean_attempts', 'mean_duration_seconds', 'mean_seconds', 'pending_for_orchestrator')
        or any(p in ('mean_attempts', 'mean_duration_seconds', 'mean_seconds', 'pending_for_orchestrator') for p in parts)
    ):
        return 'down'
    return 'up'


def get_next_id(root):
    max_id = 0
    docs_dir = root / 'docs/experiments'
    if docs_dir.is_dir() and not docs_dir.is_symlink():
        for p in docs_dir.iterdir():
            if not p.is_symlink():
                m = re.match(r'^(\d{3,})-', p.name)
                if m:
                    max_id = max(max_id, int(m.group(1)))
    ws_dir = root / '.workspace/experiments'
    if ws_dir.is_dir() and not ws_dir.is_symlink():
        for p in ws_dir.iterdir():
            if not p.is_symlink() and re.match(r'^\d{3,}$', p.name):
                max_id = max(max_id, int(p.name))
    return f"{max_id + 1:03d}"


def find_experiment_file(root, exp_id):
    docs_dir = root / 'docs/experiments'
    if not docs_dir.is_dir() or docs_dir.is_symlink():
        return None
    num = f"{int(exp_id):03d}" if str(exp_id).isdigit() else str(exp_id)
    matches = []
    for p in docs_dir.iterdir():
        if p.is_symlink():
            continue
        if p.name == f"{num}.md" or (p.name.startswith(f"{num}-") and p.name.endswith('.md')):
            matches.append(p)
    if len(matches) == 1:
        return matches[0]
    return None


def run_metrics(root):
    metrics_script = root / 'scripts/lib/metrics.py'
    if not metrics_script.is_file() or metrics_script.is_symlink():
        sys.stderr.write(f"Erreur : script introuvable ou symlink : {metrics_script}\n")
        sys.exit(1)
    cmd = [sys.executable, str(metrics_script), str(root), '--json']
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        sys.stderr.write(f"Erreur lors de l'exécution de metrics.py (code {res.returncode}) :\n{res.stderr}\n")
        sys.exit(res.returncode or 1)
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"Erreur JSON metrics.py : {e}\n{res.stdout}\n")
        sys.exit(1)


def safe_write_new(path, content, mode=0o644):
    if path.is_symlink() or path.exists():
        sys.stderr.write(f"Erreur : le fichier {path} existe déjà ou est un lien symbolique.\n")
        sys.exit(1)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(path), flags, mode)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(content)
    try:
        os.chmod(str(path), mode)
    except OSError:
        pass


def safe_write_existing(path, content):
    if path.is_symlink():
        sys.stderr.write(f"Erreur : lien symbolique non autorisé : {path}\n")
        sys.exit(1)
    flags = os.O_WRONLY | os.O_TRUNC
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(path), flags)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(content)


def format_val(val):
    if val is None:
        return 'null'
    if isinstance(val, float):
        return f"{val:.3f}"
    return str(val)


def format_diff(before_val, after_val):
    if before_val is None or after_val is None:
        return 'null', 'null'
    diff = after_val - before_val
    if diff == 0:
        return ('0.000' if isinstance(diff, float) else '0'), '0.0%'
    if isinstance(diff, float):
        diff_str = f"{diff:+.3f}"
    else:
        diff_str = f"{diff:+d}"
    if before_val != 0:
        pct = (diff / abs(before_val)) * 100
        pct_str = f"{pct:+.1f}%"
    else:
        pct_str = "0.0%" if after_val == 0 else "N/A"
    return diff_str, pct_str


def evaluate(before_val, after_val, expect):
    if before_val is None or after_val is None:
        return "inchangé"
    if after_val == before_val:
        return "inchangé"
    if expect == 'up':
        return "amélioré" if after_val > before_val else "dégradé"
    if expect == 'down':
        return "amélioré" if after_val < before_val else "dégradé"
    return "inchangé"


def cmd_start(args):
    root = args.root
    before_data = run_metrics(root)

    available = get_numeric_paths(before_data)
    if "bench" in before_data and "bench.advisor.accuracy" not in available:
        available.append("bench.advisor.accuracy")
    available = sorted(set(available))

    for m, _ in args.metric_specs:
        valid, _ = resolve_metric(before_data, m)
        if not valid:
            msg = (
                f"Erreur : indicateur introuvable '{m}'.\n"
                f"Chemins numériques disponibles :\n" + "\n".join(f"  {p}" for p in available) + "\n"
            )
            sys.stderr.write(msg)
            sys.stdout.write(msg)
            sys.exit(64)

    # Each --expect applies to the --metric just before it; others keep their default direction.
    metrics_with_expect = [(m, exp or get_default_direction(m)) for m, exp in args.metric_specs]

    exp_id = get_next_id(root)
    slug = slugify(args.hypothesis, 50)
    filename = f"{exp_id}-{slug}.md"

    docs_dir = root / 'docs/experiments'
    if docs_dir.is_symlink():
        sys.stderr.write("Erreur : docs/experiments est un lien symbolique.\n")
        sys.exit(1)
    docs_dir.mkdir(parents=True, exist_ok=True)

    target_md = docs_dir / filename
    today = datetime.date.today().isoformat()

    table_rows = []
    for m, exp in metrics_with_expect:
        val = resolve_metric(before_data, m)[1]
        table_rows.append(f"| {m} | {exp} | {format_val(val)} |")

    content = (
        f"# Expérience {exp_id} : {slug}\n\n"
        f"- **Date** : {today}\n"
        f"- **Statut** : en cours\n"
        f"- **Hypothèse** : {args.hypothesis}\n\n"
        f"## Indicateurs suivis\n\n"
        f"| Indicateur | Attendu | Avant |\n"
        f"| --- | --- | --- |\n" + "\n".join(table_rows) + "\n\n"
        f"## Garde-fous\n\n"
        f"{GARDE_FOU}\n\n"
        f"## Journal\n"
    )

    safe_write_new(target_md, content, mode=0o644)

    ws_exp_base = root / '.workspace/experiments'
    if ws_exp_base.is_symlink():
        sys.stderr.write("Erreur : .workspace/experiments est un lien symbolique.\n")
        sys.exit(1)
    ws_exp_base.mkdir(parents=True, exist_ok=True)
    try:
        ws_exp_base.chmod(0o700)
    except OSError:
        pass

    ws_exp_dir = ws_exp_base / exp_id
    if ws_exp_dir.is_symlink():
        sys.stderr.write("Erreur : le dossier de l'expérience est un lien symbolique.\n")
        sys.exit(1)
    ws_exp_dir.mkdir(parents=True, exist_ok=True)
    try:
        ws_exp_dir.chmod(0o700)
    except OSError:
        pass

    before_file = ws_exp_dir / 'before.json'
    safe_write_new(before_file, json.dumps(before_data, indent=2, ensure_ascii=False) + "\n", mode=0o600)
    print(f"Expérience {exp_id} démarrée : {target_md}")


def cmd_note(args):
    root = args.root
    exp_file = find_experiment_file(root, args.experiment_id)
    if not exp_file:
        sys.stderr.write(f"Erreur : expérience introuvable '{args.experiment_id}'.\n")
        sys.exit(1)

    content = exp_file.read_text(encoding='utf-8')
    today = datetime.date.today().isoformat()
    note_line = f"- {today} : {args.text}\n"

    if "## Journal" in content:
        parts = content.split("## Journal")
        header = parts[0] + "## Journal\n"
        rest = parts[1].lstrip('\n')
        if rest:
            rest_clean = rest if rest.endswith('\n') else rest + '\n'
            new_content = header + "\n" + rest_clean + note_line
        else:
            new_content = header + "\n" + note_line
    else:
        new_content = content.rstrip() + f"\n\n## Journal\n\n{note_line}"

    safe_write_existing(exp_file, new_content)
    print(f"Note ajoutée à l'expérience {args.experiment_id}.")


def cmd_end(args):  # noqa: C901, PLR0915
    root = args.root
    exp_file = find_experiment_file(root, args.experiment_id)
    if not exp_file:
        sys.stderr.write(f"Erreur : expérience introuvable '{args.experiment_id}'.\n")
        sys.exit(1)

    content = exp_file.read_text(encoding='utf-8')
    m_status = re.search(r'-\s*\*\*Statut\*\*\s*:\s*([^\n]+)', content)
    current_status = m_status.group(1).strip() if m_status else ""
    if current_status != "en cours":
        sys.stderr.write(f"Erreur : l'expérience est déjà close (statut actuel : '{current_status}').\n")
        sys.exit(1)

    decision_map = {
        'gardé': 'gardé',
        'garde': 'gardé',
        'abandonné': 'abandonné',
        'abandonne': 'abandonné',
        'à-refaire': 'à-refaire',
        'a-refaire': 'à-refaire',
    }
    decision = decision_map.get(args.decision, args.decision)

    num = f"{int(args.experiment_id):03d}" if str(args.experiment_id).isdigit() else str(args.experiment_id)
    ws_exp_dir = root / '.workspace/experiments' / num
    if ws_exp_dir.is_symlink() or not ws_exp_dir.is_dir():
        sys.stderr.write(f"Erreur : dossier d'expérience introuvable : {ws_exp_dir}\n")
        sys.exit(1)

    before_file = ws_exp_dir / 'before.json'
    if before_file.is_symlink() or not before_file.is_file():
        sys.stderr.write(f"Erreur : fichier {before_file} introuvable ou symlink.\n")
        sys.exit(1)
    before_data = json.loads(before_file.read_text(encoding='utf-8'))

    after_data = run_metrics(root)

    try:
        ws_exp_dir.chmod(0o700)
    except OSError:
        pass
    after_file = ws_exp_dir / 'after.json'
    safe_write_new(after_file, json.dumps(after_data, indent=2, ensure_ascii=False) + "\n", mode=0o600)

    metrics_info = []
    in_indicators = False
    for line in content.splitlines():
        if line.startswith('## Indicateurs suivis'):
            in_indicators = True
            continue
        if in_indicators:
            if line.startswith('## '):
                break
            m = re.match(r'^\|\s*([a-zA-Z0-9_.]+)\s*\|\s*(up|down)\s*\|', line)
            if m:
                metrics_info.append((m.group(1).strip(), m.group(2).strip()))

    table_lines = ["| Indicateur | Attendu | Avant | Après | Écart | Écart % | Résultat |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for metric_name, expect_dir in metrics_info:
        b_val = resolve_metric(before_data, metric_name)[1]
        a_val = resolve_metric(after_data, metric_name)[1]
        diff_str, pct_str = format_diff(b_val, a_val)
        res_str = evaluate(b_val, a_val, expect_dir)
        table_lines.append(
            f"| {metric_name} | {expect_dir} | {format_val(b_val)} | {format_val(a_val)} | {diff_str} | {pct_str} | {res_str} |"
        )
    new_table_str = "\n".join(table_lines) + "\n"

    tasks_b = resolve_metric(before_data, 'delegation.tasks')[1] or 0
    tasks_a = resolve_metric(after_data, 'delegation.tasks')[1] or 0
    delta_tasks = tasks_a - tasks_b

    questions_b = resolve_metric(before_data, 'advisor.questions')[1] or 0
    questions_a = resolve_metric(after_data, 'advisor.questions')[1] or 0
    delta_questions = questions_a - questions_b

    has_delegation = any(m.startswith('delegation.') or m == 'delegation' for m, _ in metrics_info)
    has_advisor = any(m.startswith('advisor.') or m == 'advisor' for m, _ in metrics_info)

    if has_delegation and not has_advisor:
        too_small = delta_tasks < 3
    elif has_advisor and not has_delegation:
        too_small = delta_questions < 3
    elif has_delegation and has_advisor:
        too_small = (delta_tasks < 3) or (delta_questions < 3)
    else:
        too_small = (delta_tasks < 3) and (delta_questions < 3)

    warning_str = ""
    if too_small:
        warning_str = f"\n> ⚠️ **Avertissement** : {AVERTISSEMENT_ECHANTILLON}\n"

    content = re.sub(r'-\s*\*\*Statut\*\*\s*:\s*[^\n]+', f"- **Statut** : {decision}", content)

    pattern = r'(## Indicateurs suivis\s*\n\s*)([\s\S]*?)(\n## |\Z)'
    replacement = r'\1\n' + new_table_str + warning_str + r'\3'
    content = re.sub(pattern, replacement, content)

    decision_section = f"## Décision : {decision}\n\n"
    if args.reason:
        decision_section += f"**Raison** : {args.reason}\n\n"

    if "## Décision" in content:
        content = re.sub(r'## Décision[^\n]*\n(?:[^\n#]+\n*)*', decision_section, content)
    elif "## Garde-fous" in content:
        content = content.replace("## Garde-fous", decision_section + "## Garde-fous")
    elif "## Journal" in content:
        content = content.replace("## Journal", decision_section + "## Journal")
    else:
        content = content.rstrip() + f"\n\n{decision_section}"

    safe_write_existing(exp_file, content)
    print(f"Expérience {args.experiment_id} close avec décision '{decision}'.")


def cmd_list(args):
    root = args.root
    docs_dir = root / 'docs/experiments'
    if not docs_dir.is_dir() or docs_dir.is_symlink():
        return
    files = []
    for p in docs_dir.iterdir():
        if p.is_symlink():
            continue
        m = re.match(r'^(\d{3,})-.*\.md$', p.name)
        if m:
            files.append((int(m.group(1)), p))
    files.sort(key=lambda x: x[0])

    for _, p in files:
        num = p.name[:3]
        content = p.read_text(encoding='utf-8')
        m_status = re.search(r'-\s*\*\*Statut\*\*\s*:\s*([^\n]+)', content)
        status = m_status.group(1).strip() if m_status else "inconnu"

        m_date = re.search(r'-\s*\*\*Date\*\*\s*:\s*([^\n]+)', content)
        date = m_date.group(1).strip() if m_date else "inconnue"

        m_hyp = re.search(r'-\s*\*\*Hypothèse\*\*\s*:\s*([^\n]+)', content)
        hyp = m_hyp.group(1).strip() if m_hyp else ""
        short_hyp = (hyp[:37] + '...') if len(hyp) > 40 else hyp

        main_diff = ""
        if status != "en cours":
            row_match = re.search(
                r'\|\s*([a-zA-Z0-9_.]+)\s*\|\s*(up|down)\s*\|\s*[^|]+\|\s*[^|]+\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]+)\|', content
            )
            if row_match:
                metric = row_match.group(1).strip()
                diff_val = row_match.group(3).strip()
                pct_val = row_match.group(4).strip()
                main_diff = f"{metric}: {diff_val} ({pct_val})"

        line = f"{num}  {status:<11}  {date:<10}  {short_hyp:<40}  {main_diff}".rstrip()
        print(line)


class MetricAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        specs = list(getattr(namespace, self.dest, None) or [])
        setattr(namespace, self.dest, specs + [(values, None)])


class ExpectAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        specs = list(getattr(namespace, self.dest, None) or [])
        if not specs or specs[-1][1] is not None:
            parser.error('--expect doit suivre un --metric')
        setattr(namespace, self.dest, specs[:-1] + [(specs[-1][0], values)])


def main():
    parser = argparse.ArgumentParser(description="Cahier d'expériences du workspace")
    parser.add_argument('root', type=Path, help='Racine du workspace')
    subparsers = parser.add_subparsers(dest='command', required=True)

    parser_start = subparsers.add_parser('start', help='Démarrer une expérience')
    parser_start.add_argument('hypothesis', type=str, help='Hypothèse testée')
    parser_start.add_argument('--metric', dest='metric_specs', action=MetricAction, required=True, help='Indicateur à suivre')
    parser_start.add_argument(
        '--expect', dest='metric_specs', action=ExpectAction, choices=['up', 'down'], help="Sens attendu pour l'indicateur précédent"
    )

    parser_note = subparsers.add_parser('note', help='Ajouter une note au journal')
    parser_note.add_argument('experiment_id', type=str, help='Numéro NNN')
    parser_note.add_argument('text', type=str, help='Texte de la note')

    parser_end = subparsers.add_parser('end', help='Clore une expérience')
    parser_end.add_argument('experiment_id', type=str, help='Numéro NNN')
    parser_end.add_argument(
        '--decision', required=True, choices=['gardé', 'abandonné', 'à-refaire', 'garde', 'abandonne', 'a-refaire'], help='Décision prise'
    )
    parser_end.add_argument('--reason', default='', help='Raison de la décision')

    subparsers.add_parser('list', help='Lister les expériences')

    args = parser.parse_args()

    if args.command == 'start':
        cmd_start(args)
    elif args.command == 'note':
        cmd_note(args)
    elif args.command == 'end':
        cmd_end(args)
    elif args.command == 'list':
        cmd_list(args)


if __name__ == '__main__':
    main()
