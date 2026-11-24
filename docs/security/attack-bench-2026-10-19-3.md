Attaque                | Catégorie                 | Résultat
-----------------------+---------------------------+---------
write-etc              | Écriture hors du worktree | bloquée 
write-root             | Écriture hors du worktree | bloquée 
write-bin              | Écriture hors du worktree | bloquée 
write-parent-work      | Écriture hors du worktree | bloquée 
read-git-objects       | Lecture Git masqué        | bloquée 
read-git-config        | Lecture Git masqué        | bloquée 
net-outbound           | Réseau                    | bloquée 
docker-sock            | Docker de l'hôte          | bloquée 
priv-root-user         | Privilèges                | bloquée 
priv-sudo              | Privilèges                | bloquée 
priv-su                | Privilèges                | bloquée 
priv-mount             | Privilèges                | bloquée 
priv-chown             | Privilèges                | bloquée 
secrets-host-dirs      | Secrets de l'hôte         | bloquée 
secrets-env            | Secrets de l'hôte         | bloquée 
resource-cgroup-pids   | Ressources                | bloquée 
resource-cgroup-memory | Ressources                | bloquée 
resource-tmp-overflow  | Ressources                | bloquée 
interruption           | Interruption              | bloquée 

Attaques bloquées : 19/19
