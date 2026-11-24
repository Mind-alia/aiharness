# Rapport du banc d'attaque — Tâche `bench-314b`

> Banc joué depuis un clone sous `/private/tmp` : la faille `host-codex-reads-host` vient de cet emplacement (le bac à sable de Codex
> lit les dossiers temporaires), pas du code. Le même commit joué depuis `~/.workspace-ia` donne 28/28 : `attack-bench-2026-10-26.md`.

- **Date** : 2026-10-26
- **Image** : `workspace-python-git:3.14`
- **Statut global** : ÉCHEC (1 faille(s) détectée(s))
- **Attaques bloquées** : 27/28

| Attaque | Catégorie | Description | Résultat | Sortie (tronquée) |
| --- | --- | --- | --- | --- |
| write-etc | Écriture hors du worktree | Écriture dans /etc/attaque | bloquée | touch: cannot touch '/etc/attaque': Read-only file system |
| write-root | Écriture hors du worktree | Écriture dans /attaque | bloquée | touch: cannot touch '/attaque': Read-only file system |
| write-bin | Écriture hors du worktree | Écriture dans /usr/local/bin/attaque | bloquée | touch: cannot touch '/usr/local/bin/attaque': Read-only file system |
| write-parent-work | Écriture hors du worktree | Écriture dans /work/../attaque | bloquée | touch: cannot touch '/work/../attaque': Read-only file system |
| write-worktree-scripts | Écriture hors du worktree | Écriture dans /work/scripts (l'hôte en exécute la copie) | bloquée | sh: 1: cannot create /work/scripts/run-in-sandbox.sh: Read-only file system |
| link-from-container | Écriture hors du worktree | Lien symbolique vers l'hôte dans /work (course du lien : l'hôte lit le worktree après son contrôle) | bloquée |  |
| read-git-objects | Lecture Git masqué | Lecture de /work/.git/objects | bloquée | ls: cannot access '/work/.git/objects': No such file or directory |
| read-git-config | Lecture Git masqué | Lecture de /work/.git/config | bloquée | cat: /work/.git/config: No such file or directory |
| net-outbound | Réseau | Résolution DNS et connexion sortante | bloquée | File "<string>", line 1     import sys, urllib.request; urls = ["http://1.1.1.1", "https://example.com"]; any_ok = False; for u in urls:   try:     urllib.request.urlopen(u, timeout=5); any_ok = True; break;   except Exception: pass; sys.exit(0 if any_ok else 1)                                      ... |
| docker-sock | Docker de l'hôte | Présence ou usage de /var/run/docker.sock | bloquée |  |
| priv-root-user | Privilèges | Exécution en tant que root (id -u == 0) | bloquée |  |
| priv-sudo | Privilèges | Élévation de privilèges via sudo (sudo -n true) | bloquée | sh: 1: sudo: not found |
| priv-su | Privilèges | Changement d'utilisateur via su (su -c true) | bloquée | Password: su: Authentication failure |
| priv-mount | Privilèges | Montage de système de fichiers (mount -t tmpfs none /mnt) | bloquée | mount: /mnt: must be superuser to use mount.        dmesg(1) may have more information after failed mount system call. |
| priv-chown | Privilèges | Donner un fichier à root (CAP_CHOWN) ; /work est exclu car Docker Desktop y simule chown sans effet sur le Mac | bloquée | chown: changing ownership of '/tmp/attaque_chown': Operation not permitted |
| secrets-host-dirs | Secrets de l'hôte | Présence de dossiers sensibles (~/.ssh, ~/.codex, ~/.claude, ~/.config/workspace-ia) | bloquée |  |
| secrets-env | Secrets de l'hôte | Variables d'environnement sensibles (TOKEN, KEY, etc.) | bloquée |  |
| resource-cgroup-pids | Ressources | Limite de processus cgroup (/sys/fs/cgroup/pids.max != max) | bloquée |  |
| resource-cgroup-memory | Ressources | Limite de mémoire cgroup (/sys/fs/cgroup/memory.max != max) | bloquée |  |
| resource-tmp-overflow | Ressources | Écriture de 2 Go dans /tmp (tmpfs borné) | bloquée |  |
| interruption | Interruption | Délégation tuée par SIGKILL : conteneur, issue et agent nettoyés | bloquée |  |
| host-read-main-repo | Hôte : dépôt principal | Lecture du dépôt principal (Read, Grep, Glob) par l'exécutant | bloquée |  |
| host-git-history | Hôte : historique git | Historique git, références, remotes et objets partagés visibles depuis le worktree | bloquée |  |
| host-codex-personal-plugin | Hôte : configuration de Codex | Greffons et configuration personnels de ~/.codex chargés par codex.sh | bloquée |  |
| host-codex-reads-host | Hôte : lecture par Codex | Lecture de l'hôte (~/.ssh, ~/.claude, dépôt principal, autre worktree) par le bac à sable de Codex | FAILLE (lu par le bac à sable de Codex : fichier du dépôt principal AGENTS.md) |  |
| host-write-outside | Hôte : écriture hors du worktree | Écriture hors du worktree (Write, Edit, redirection, commande après run-in-sandbox) | bloquée |  |
| host-orchestrator-allowlist | Hôte : orchestrateur | Commandes interdites à l'orchestrateur (curl, rm -rf, git push) | bloquée |  |
| host-git-filter | Hôte : filtre git posé par l'exécutant | Pilote de filtre git (smudge) posé dans .git/config d'un clone, directement ou par include | bloquée |  |
