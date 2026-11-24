"""Mise en forme texte et Markdown du tableau de bord (Python 3.14, standard library uniquement)."""

import re


def fmt_int(n):
    if n is None:
        return "-"
    return f"{n:,}".replace(",", " ")


def fmt_float(n, decimals=1):
    if n is None:
        return "-"
    return f"{n:,.{decimals}f}".replace(",", " ")


def fmt_pct(rate):
    if rate is None:
        return "-"
    return f"{rate * 100:.1f} %"


def format_dict_summary(d):
    if not d:
        return "aucun"
    return ", ".join(f"{k}: {fmt_int(v)}" for k, v in sorted(d.items()))


def format_bench_summary(bench):
    if not bench:
        return "aucun"
    if not isinstance(bench, dict):
        return "illisible"

    def pct(value):
        return "-" if not isinstance(value, (int, float)) else "%d %%" % round(100 * value)

    seconds = bench.get("mean_seconds")
    return "%s/%s justes (%s), « je ne sais pas » justes %s/%s, passage fourni %s, %s s par question, le %s" % (
        bench.get("correct", "-"),
        bench.get("answerable", "-"),
        pct(bench.get("accuracy")),
        bench.get("abstained", "-"),
        bench.get("unanswerable", "-"),
        pct(bench.get("passage_recall")),
        "-" if not isinstance(seconds, (int, float)) else "%.1f" % seconds,
        str(bench.get("at", "-"))[:10],
    )


def format_text(data):
    p = data["project"]
    dt = data["generated_at"][:10]
    since = f" (depuis {data['since']})" if data.get("since") else ""
    lines = [
        f"Tableau de bord — {p} ({dt}){since}",
        "=" * 60,
        "Délégation :",
        f"  Tâches : {fmt_int(data['delegation']['tasks'])} | Réussite 1er essai : {fmt_pct(data['delegation']['first_try_success_rate'])} | Tentatives moy. : {fmt_float(data['delegation']['mean_attempts'])}",
        f"  Durée moy. : {fmt_float(data['delegation']['mean_duration_seconds'])}s | Fusionnées : {fmt_int(data['delegation']['merged'])} | Abandonnées : {fmt_int(data['delegation']['abandoned'])}",
        f"  Résultats : {format_dict_summary(data['delegation']['outcomes'])} | États finaux : {format_dict_summary(data['delegation']['final_states'])}",
        "Conseiller :",
        f"  Questions : {fmt_int(data['advisor']['questions'])} | Taux de réponse : {fmt_pct(data['advisor']['answer_rate'])} | Temps moy. : {fmt_float(data['advisor']['mean_seconds'])}s",
        f"  Répondues : {fmt_int(data['advisor']['answered'])} | Inconnues : {fmt_int(data['advisor']['unknown'])} | Indisponibles : {fmt_int(data['advisor']['unavailable'])} | Recherche : {fmt_int(data['advisor']['research'])}",
        f"  En attente pour l'orchestrateur : {fmt_int(data['advisor']['pending_for_orchestrator'])}",
        "Tokens :",
        f"  Claude : {fmt_int(data['tokens']['claude']['messages'])} msgs | In : {fmt_int(data['tokens']['claude']['input'])} | Cache créa : {fmt_int(data['tokens']['claude']['cache_creation'])} | Cache lect : {fmt_int(data['tokens']['claude']['cache_read'])} | Out : {fmt_int(data['tokens']['claude']['output'])}",
    ]
    cl_orch = data.get('tokens', {}).get('claude_orchestrator', {})
    if cl_orch and cl_orch.get('messages', 0) > 0:
        lines.append(
            f"  Claude orch. : {fmt_int(cl_orch['messages'])} msgs | In : {fmt_int(cl_orch['input'])} | Cache créa : {fmt_int(cl_orch['cache_creation'])} | Cache lect : {fmt_int(cl_orch['cache_read'])} | Out : {fmt_int(cl_orch['output'])}"
        )

    lines.append(
        f"  Codex  : {fmt_int(data['tokens']['codex']['turns'])} tours | In : {fmt_int(data['tokens']['codex']['input'])} | Cached in : {fmt_int(data['tokens']['codex']['cached_input'])} | Out : {fmt_int(data['tokens']['codex']['output'])} | Raisonnement : {fmt_int(data['tokens']['codex']['reasoning_output'])}"
    )

    lines.append("Consommation par rôle :")
    cons = data.get("consumption", {})
    by_role = cons.get("by_role", {})
    if by_role:
        headers = [
            "Rôle",
            "Appels",
            "Entrée native",
            "Cache lecture",
            "Cache création",
            "Sortie",
            "Total tokens",
            "Incomplets",
            "Proxy",
            "Durée (s)",
        ]
        rows = []
        for role, rinfo in sorted(by_role.items()):
            rows.append(
                [
                    role,
                    fmt_int(rinfo.get("calls", 0)),
                    fmt_int(rinfo.get("input", 0)),
                    fmt_int(rinfo.get("cached_input", 0)),
                    fmt_int(rinfo.get("cache_creation")),
                    fmt_int(rinfo.get("output", 0)),
                    fmt_int(rinfo.get("total_tokens")),
                    fmt_int(rinfo.get("unknown_token_calls", 0)),
                    fmt_int(rinfo.get("proxy_calls", 0)),
                    fmt_float(rinfo.get("duration_seconds", 0.0)),
                ]
            )
        widths = [max(len(h), max(len(row[i]) for row in rows)) for i, h in enumerate(headers)]
        fmt_h = "  " + " | ".join(f"{h:<{widths[i]}}" for i, h in enumerate(headers))
        sep = "  " + "-+-".join("-" * widths[i] for i in range(len(headers)))
        lines.append(fmt_h)
        lines.append(sep)
        for row in rows:
            lines.append("  " + " | ".join(f"{col:<{widths[i]}}" for i, col in enumerate(row)))
    else:
        lines.append("  Aucune consommation enregistrée.")

    q_count = cons.get("quota_limits", 0)
    last_q = cons.get("last_quota_limit")
    if q_count > 0 and last_q:
        dt_str = str(last_q.get("at", ""))[:10]
        prov_str = str(last_q.get("provider", "-"))
        lines.append(f"  Limites de quota atteintes : {q_count} (dernière : {dt_str}, {prov_str})")
    else:
        lines.append(f"  Limites de quota atteintes : {q_count}")

    lines.extend(
        [
            "Git :",
            f"  Commits : {fmt_int(data['git']['commits'])} | Merges : {fmt_int(data['git']['merges'])}",
            "Banc d'essai :",
            f"  Conseiller : {format_bench_summary(data['bench']['advisor'])}",
        ]
    )
    return "\n".join(lines)


def reading_role_cell(role):
    """Rôle sur une seule cellule : coupé à la première fin de ligne, `|` écrit `\\|`."""
    return re.split(r'[\r\n]', role, maxsplit=1)[0].replace('|', '\\|')


def format_reading(by_role):
    lines = ["\n## Lecture avant d'agir, par rôle", "Moyenne par délégation ; tokens estimés = caractères / 4 (estimation, pas un compte)."]
    if not by_role:
        return lines + ["- aucune mesure"]
    lines += ["| Rôle | Délégations | Consigne commune | Fiche | Pièges |", "| --- | --- | --- | --- | --- |"]
    for role, info in sorted(by_role.items()):
        cells = ['%d car. (~%d tokens)' % (info[part + '_chars'], info[part + '_chars'] // 4) for part in ('common', 'task', 'pitfalls')]
        lines.append('| ' + ' | '.join([reading_role_cell(role), str(info['delegations'])] + cells) + ' |')
    return lines


def format_markdown(data):  # noqa: C901
    p = data["project"]
    dt = data["generated_at"][:10]
    since = f" (depuis {data['since']})" if data.get("since") else ""
    md = [
        f"# Tableau de bord — {p} ({dt}){since}\n",
        "## Délégation",
        f"- **Tâches** : {fmt_int(data['delegation']['tasks'])}",
        f"- **Réussite au 1er essai** : {fmt_pct(data['delegation']['first_try_success_rate'])}",
        f"- **Tentatives moyennes** : {fmt_float(data['delegation']['mean_attempts'])}",
        f"- **Durée moyenne** : {fmt_float(data['delegation']['mean_duration_seconds'])}s",
        f"- **Fusionnées** : {fmt_int(data['delegation']['merged'])} | **Abandonnées** : {fmt_int(data['delegation']['abandoned'])}",
        f"- **Résultats** : {format_dict_summary(data['delegation']['outcomes'])}",
        f"- **États finaux** : {format_dict_summary(data['delegation']['final_states'])}",
    ]
    if data["delegation"]["by_agent"]:
        md.extend(
            [
                "\n### Par agent",
                "| Agent | Tâches | 1er essai | Tentatives moy. | Durée moy. (s) | Tokens |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for agent, ainfo in sorted(data["delegation"]["by_agent"].items()):
            md.append(
                f"| {agent} | {fmt_int(ainfo['tasks'])} | {fmt_pct(ainfo['first_try_success_rate'])} | {fmt_float(ainfo['mean_attempts'])} | {fmt_float(ainfo['mean_duration_seconds'])} | {fmt_int(ainfo['tokens'])} |"
            )

    md.extend(format_reading(data.get("reading")))
    md.extend(
        [
            "\n## Conseiller",
            f"- **Questions** : {fmt_int(data['advisor']['questions'])}",
            f"- **Taux de réponse** : {fmt_pct(data['advisor']['answer_rate'])}",
            f"- **Temps moyen** : {fmt_float(data['advisor']['mean_seconds'])}s",
            f"- **Répondues** : {fmt_int(data['advisor']['answered'])} | **Inconnues** : {fmt_int(data['advisor']['unknown'])} | **Indisponibles** : {fmt_int(data['advisor']['unavailable'])} | **Recherche** : {fmt_int(data['advisor']['research'])}",
            f"- **En attente pour l'orchestrateur** : {fmt_int(data['advisor']['pending_for_orchestrator'])}",
            "\n## Tokens",
            "### Claude",
            f"- **Messages** : {fmt_int(data['tokens']['claude']['messages'])}",
            f"- **Input** : {fmt_int(data['tokens']['claude']['input'])} | **Cache création** : {fmt_int(data['tokens']['claude']['cache_creation'])} | **Cache lecture** : {fmt_int(data['tokens']['claude']['cache_read'])} | **Output** : {fmt_int(data['tokens']['claude']['output'])}",
        ]
    )
    if data["tokens"]["claude"]["by_model"]:
        md.extend(
            [
                "\n#### Par modèle (Claude)",
                "| Modèle | Messages | Input | Cache créa | Cache lect | Output |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for model, minfo in sorted(data["tokens"]["claude"]["by_model"].items()):
            md.append(
                f"| {model} | {fmt_int(minfo['messages'])} | {fmt_int(minfo['input'])} | {fmt_int(minfo['cache_creation'])} | {fmt_int(minfo['cache_read'])} | {fmt_int(minfo['output'])} |"
            )

    cl_orch = data.get('tokens', {}).get('claude_orchestrator', {})
    if cl_orch and cl_orch.get('messages', 0) > 0:
        md.extend(
            [
                "\n### Claude (orchestrateur)",
                f"- **Messages** : {fmt_int(cl_orch['messages'])}",
                f"- **Input** : {fmt_int(cl_orch['input'])} | **Cache création** : {fmt_int(cl_orch['cache_creation'])} | **Cache lecture** : {fmt_int(cl_orch['cache_read'])} | **Output** : {fmt_int(cl_orch['output'])}",
            ]
        )
        if cl_orch.get("by_model"):
            md.extend(
                [
                    "\n#### Par modèle (Claude orchestrateur)",
                    "| Modèle | Messages | Input | Cache créa | Cache lect | Output |",
                    "| --- | --- | --- | --- | --- | --- |",
                ]
            )
            for model, minfo in sorted(cl_orch["by_model"].items()):
                md.append(
                    f"| {model} | {fmt_int(minfo['messages'])} | {fmt_int(minfo['input'])} | {fmt_int(minfo['cache_creation'])} | {fmt_int(minfo['cache_read'])} | {fmt_int(minfo['output'])} |"
                )

    md.extend(
        [
            "\n### Codex",
            f"- **Tours** : {fmt_int(data['tokens']['codex']['turns'])}",
            f"- **Input** : {fmt_int(data['tokens']['codex']['input'])} | **Cached input** : {fmt_int(data['tokens']['codex']['cached_input'])} | **Output** : {fmt_int(data['tokens']['codex']['output'])} | **Raisonnement** : {fmt_int(data['tokens']['codex']['reasoning_output'])}",
            "\n## Consommation par rôle",
        ]
    )

    cons = data.get("consumption", {})
    by_role = cons.get("by_role", {})
    if by_role:
        md.extend(
            [
                "| Rôle | Appels | Entrée native | Cache lecture | Cache création | Sortie | Total tokens | Incomplets | Proxy | Durée (s) |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for role, rinfo in sorted(by_role.items()):
            md.append(
                f"| {role} | {fmt_int(rinfo.get('calls', 0))} | {fmt_int(rinfo.get('input', 0))} | {fmt_int(rinfo.get('cached_input', 0))} | {fmt_int(rinfo.get('cache_creation'))} | {fmt_int(rinfo.get('output', 0))} | {fmt_int(rinfo.get('total_tokens'))} | {fmt_int(rinfo.get('unknown_token_calls', 0))} | {fmt_int(rinfo.get('proxy_calls', 0))} | {fmt_float(rinfo.get('duration_seconds', 0.0))} |"
            )
    else:
        md.append("- Aucune consommation enregistrée.")

    q_count = cons.get("quota_limits", 0)
    last_q = cons.get("last_quota_limit")
    if q_count > 0 and last_q:
        dt_str = str(last_q.get("at", ""))[:10]
        prov_str = str(last_q.get("provider", "-"))
        md.append(f"- **Limites de quota atteintes** : {q_count} (dernière : {dt_str}, {prov_str})")
    else:
        md.append(f"- **Limites de quota atteintes** : {q_count}")

    md.extend(
        [
            "\n## Git",
            f"- **Commits** : {fmt_int(data['git']['commits'])}",
            f"- **Merges** : {fmt_int(data['git']['merges'])}",
            "\n## Banc d'essai",
            f"- **Conseiller** : {format_bench_summary(data['bench']['advisor'])}",
        ]
    )
    return "\n".join(md) + "\n"
