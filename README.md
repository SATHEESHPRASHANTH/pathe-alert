# 🎬 Surveillance Pathé Brumath

Script automatisé qui surveille la disponibilité des séances pour un film spécifique au cinéma Pathé Brumath et envoie un email d'alerte uniquement lors de la transition **indisponible → disponible**.

## 📋 Fonctionnalités

- ✅ Surveillance automatique toutes les 5 minutes via GitHub Actions
- ✅ Détection robuste de la disponibilité (mot-clé cinéma + signaux de réservation + horaires)
- ✅ Envoi d'email uniquement lors de la transition (anti-spam)
- ✅ Persistance de l'état entre les exécutions (cache GitHub Actions)
- ✅ Logs détaillés avec timestamps UTC
- ✅ Gestion d'erreurs robuste (timeouts, pages inaccessibles)

## 🚀 Installation et Configuration

### 1. Créer le dépôt GitHub

1. Créez un nouveau dépôt GitHub (public ou privé)
2. Clonez-le localement :
   ```bash
   git clone <url-du-repo>
   cd pathe-alert2
   ```
3. Copiez tous les fichiers du projet dans le dépôt
4. Commitez et poussez :
   ```bash
   git add .
   git commit -m "Initial commit: surveillance Pathé Brumath"
   git push origin main
   ```

### 2. Configurer les secrets GitHub

Allez dans **Settings → Secrets and variables → Actions** de votre dépôt et ajoutez les secrets suivants :

| Secret | Description | Exemple |
|--------|-------------|---------|
| `BREVO_SMTP_USER` | Identifiant SMTP Brevo | `xxxx@smtp-brevo.com` |
| `BREVO_SMTP_KEY` | Clé SMTP Brevo | `votre-clé-secrète` |
| `BREVO_FROM_EMAIL` | Email expéditeur validé dans Brevo | `votre-email@example.com` |
| `ALERT_TO_EMAIL` | Email destinataire (optionnel, défaut: satheeshprashanth2002@gmail.com) | `satheeshprashanth2002@gmail.com` |

**Comment obtenir les identifiants Brevo :**
1. Créez un compte sur [Brevo](https://www.brevo.com) (gratuit jusqu'à 300 emails/jour)
2. Allez dans **SMTP & API → SMTP**
3. Créez une clé SMTP
4. Utilisez l'identifiant au format `xxxx@smtp-brevo.com` et la clé générée
5. Validez votre adresse email expéditrice dans Brevo

### 3. Tester le workflow

1. Allez dans l'onglet **Actions** de votre dépôt GitHub
2. Sélectionnez le workflow "Surveillance Pathé Brumath"
3. Cliquez sur **Run workflow** → **Run workflow**
4. Attendez quelques secondes puis cliquez sur le run pour voir les logs

### 4. Vérifier les logs

Les logs sont disponibles dans :
- **Actions** → Sélectionner le dernier run → **check-pathe** → **Run Pathé availability check**

Les logs affichent :
- Le statut précédent et le nouveau statut
- Les détails de détection (présence du cinéma, signaux de réservation, nombre d'horaires)
- Les erreurs éventuelles
- La confirmation d'envoi d'email si applicable

## 🎯 Changer de film

Pour surveiller un autre film, modifiez les constantes dans `check_pathe.py` :

```python
FILM_NAME = "Votre nouveau film"
FILM_URL = "https://www.pathe.fr/films/votre-film-xxxxx"
CINEMA_KEYWORD = "Brumath"  # Ou un autre cinéma
```

Puis commitez et poussez les changements :
```bash
git add check_pathe.py
git commit -m "Changement de film surveillé"
git push origin main
```

## 🔍 Logique de détection

Le script considère qu'une séance est **disponible** si :
1. ✅ Le mot-clé du cinéma (ex: "Brumath") est présent dans la page
2. ✅ ET (un signal de réservation est détecté OU au moins un horaire HH:MM est trouvé)

**Signaux de réservation détectés :** "réserver", "e-billet", "billetterie"

**Horaires :** Format HH:MM (ex: 14:30, 20:15)

## 📧 Envoi d'email

L'email est envoyé **uniquement** lors de la transition :
- ❌ **Indisponible** → ✅ **Disponible**

Si le statut reste "disponible" lors des exécutions suivantes, aucun email n'est envoyé (anti-spam).

L'email contient :
- Le nom du film et le cinéma
- L'URL directe vers la page
- Les informations de détection (debug)
- La date/heure de détection (UTC)

## ⚙️ Configuration GitHub Actions

- **Fréquence :** Toutes les 5 minutes (cron en UTC)
- **Déclenchement manuel :** Disponible via "Run workflow"
- **Cache :** Le fichier `state.json` est conservé entre les runs pour éviter les emails en double

**Note :** Le cron GitHub Actions peut avoir un léger délai (quelques minutes). Les exécutions ne sont pas garanties à la seconde près.

## 🛠️ Structure du projet

```
pathe-alert2/
├── check_pathe.py              # Script principal
├── requirements.txt            # Dépendances Python
├── .github/
│   └── workflows/
│       └── pathe-alert.yml     # Workflow GitHub Actions
├── README.md                   # Cette documentation
└── state.json                  # État persistant (généré automatiquement)
```

## 🐛 Dépannage

### Le workflow ne s'exécute pas
- Vérifiez que le cron est bien configuré (format UTC)
- Les workflows peuvent être désactivés si le dépôt est inactif (réactivez-les dans Settings → Actions)

### Aucun email reçu
- Vérifiez que tous les secrets sont correctement configurés
- Consultez les logs du workflow pour voir les erreurs éventuelles
- Vérifiez que l'email expéditeur est validé dans Brevo
- Vérifiez les spams de votre boîte mail

### Erreur Playwright
- Le workflow installe automatiquement Chromium
- Si problème persistant, vérifiez les logs pour les détails

### Le cache ne fonctionne pas
- Le cache utilise `github.run_id` pour une clé unique par run
- Les `restore-keys` permettent de restaurer le dernier état disponible
- Le cache est sauvegardé automatiquement en fin de job

## 📝 Notes importantes

- ⏰ Le script utilise le fuseau horaire UTC pour tous les timestamps
- 🔒 Les identifiants SMTP ne doivent **jamais** être mis en dur dans le code
- 📊 Le fichier `state.json` est automatiquement géré par le cache GitHub Actions
- 🎯 Le script est conçu pour fonctionner en mode headless (sans interface graphique)

## 📄 Licence

Ce projet est fourni tel quel, à des fins personnelles.

