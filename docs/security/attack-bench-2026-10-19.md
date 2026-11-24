# Rapport du banc d'attaque — Tâche `attack-proof-4`

- **Date** : 2026-10-19
- **Image** : `workspace-python-git:bookworm`
- **Statut global** : SUCCÈS (aucune faille)
- **Attaques bloquées** : 19/19

| Attaque | Catégorie | Description | Résultat | Sortie (tronquée) |
| --- | --- | --- | --- | --- |
| write-etc | Écriture hors du worktree | Écriture dans /etc/attaque | bloquée | touch: cannot touch '/etc/attaque': Read-only file system |
| write-root | Écriture hors du worktree | Écriture dans /attaque | bloquée | touch: cannot touch '/attaque': Read-only file system |
| write-bin | Écriture hors du worktree | Écriture dans /usr/local/bin/attaque | bloquée | touch: cannot touch '/usr/local/bin/attaque': Read-only file system |
| write-parent-work | Écriture hors du worktree | Écriture dans /work/../attaque | bloquée | touch: cannot touch '/work/../attaque': Read-only file system |
| read-git-objects | Lecture Git masqué | Lecture de /work/.git/objects | bloquée | ls: cannot access '/work/.git/objects': Not a directory |
| read-git-config | Lecture Git masqué | Lecture de /work/.git/config | bloquée | cat: /work/.git/config: Not a directory |
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
