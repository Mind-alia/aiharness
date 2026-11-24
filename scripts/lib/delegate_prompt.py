"""Instructions handed to the executor of a task sheet (part of delegate.py).

The prompt stacks the workshop, the shared briefing and pitfalls, the allowed scope, the verify command, the
sandbox or file-exchange rules, the sheet body, answers given late and the report section. secret_origins names
where a secret found in the prompt came from, so the owner can remove it at its source.
"""

import os
from pathlib import Path
from secrets import find_secrets

import antigravity_files
import questions
from delegate_run import REPORT_SECTION, workshop_section

SHARED_DOCS = ('docs/BRIEF-AGENTS.md', 'docs/PIEGES.md')
PITFALLS = 'docs/PIEGES.md'
# Prefixes of the failure output appended to the prompt before a retry; secret_origins looks for the first one.
VERIFY_FAILURE = 'Échec de vérification, corriger :\n'
ACCEPTANCE_FAILURE = 'Échec d’acceptation, corriger :\n'
EXCHANGE_RULES = (
    "Environnement de tâche isolé (niveau exchange) :\n"
    "- Pour exécuter toute commande dans le conteneur de la tâche, lance :\n"
    "  bash scripts/run-in-sandbox.sh -- <commande>\n"
    "- Pour poser une question à l'orchestrateur, lance :\n"
    "  bash scripts/ask.sh \"<question>\"\n"
    "- Ne cherche jamais à sortir du conteneur ni à accéder au dépôt principal directement."
)


def opening(root, task, isolation, work_dir):
    """Where the executor works: its worktree when it stays on the host in exchange mode, else the project root."""
    if isolation == 'exchange' and not antigravity_files.active(task):
        # The agent process stays on the host: it is told its workshop is the worktree, root included.
        return [workshop_section(work_dir), 'Racine absolue du worktree : ' + str(work_dir)]
    return ['Racine absolue du projet : ' + str(root)]


def build(root, task, isolation, work_dir, warnings):
    """Return the prompt, the pitfalls text it carries and the late answers it injects.

    A missing shared document adds a line to warnings. The late answers are marked read by the caller just
    before the first agent call.
    """
    sections = opening(root, task, isolation, work_dir)
    pitfalls_text = ''
    for name in SHARED_DOCS:
        path = root / name
        if path.is_file():
            sections.append(path.read_text(encoding='utf-8'))
            if name == PITFALLS:
                pitfalls_text = sections[-1]
        else:
            warnings.append('Fichier facultatif absent : ' + name)
    sections.extend(
        [
            'Périmètre autorisé :\n' + '\n'.join('- ' + name for name in task['allowed']),
            'Commande de vérification : ' + (task.get('verify') or 'vérification du profil du projet'),
        ]
    )
    if antigravity_files.active(task):
        sections.append(antigravity_files.prompt_section(task, work_dir))
    elif isolation == 'exchange':
        sections.append(EXCHANGE_RULES)
    # Answers given after the agent's wait expired.
    resume_rows = questions.unread_answers(root, task['id'], worktree=work_dir if isolation == 'exchange' else None)
    sections.append(task['body'])
    if resume_rows:
        sections.append(questions.format_resume(resume_rows))
    sections.append(REPORT_SECTION)
    prompt = '\n\n'.join(sections)
    stage_context = os.environ.get('WORKSPACE_STAGE_CONTEXT')
    if stage_context:
        prompt += '\n\n' + Path(stage_context).read_text(encoding='utf-8')
    return prompt, pitfalls_text, resume_rows


def secret_origins(root, task_path, prompt, values):
    """Name file:line of each secret the prompt carries: the sheet, a shared document or the verify output."""
    sources = [(str(task_path), task_path.read_text(encoding='utf-8'))]
    sources += [(name, (root / name).read_text(encoding='utf-8')) for name in SHARED_DOCS if (root / name).is_file()]
    sources.append(('verify.txt', prompt.split(VERIFY_FAILURE)[-1] if VERIFY_FAILURE in prompt else ''))
    origins = []
    for name, content in sources:
        for hit in find_secrets(content, values):
            origin = '%s:%d' % (name, content.count('\n', 0, hit['start']) + 1)
            if origin not in origins:
                origins.append(origin)
    return origins or ['prompt.md:1']
