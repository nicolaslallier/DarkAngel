# Fournisseurs de services: spécification

Date: 2026-10-10
Statut: en attente de révision

## 1. But

Gérer les fournisseurs de services de la maison (Hydro, Bell, etc.): leurs fiches, leurs
factures, les échéances à venir et l'évolution des coûts. Les données sont partagées au
sein d'un foyer (plusieurs comptes DarkAngel).

Ce spec couvre deux parties livrées par un seul plan d'implémentation, décrites dans des
sections séparées: les **foyers** (§4) et les **fournisseurs** (§5 à §9).

### Hors périmètre

- Notifications par courriel et push: demandées à l'Infra
  (`docs/infra-feature-request-notifications.md`). Les rappels de cette version
  s'affichent seulement dans l'application.
- Partage des PDF dans la page Fichiers: un PDF reste dans les fichiers de la personne qui
  l'a téléversé.
- Devises autres que le dollar canadien, appartenance à plusieurs foyers, rôles autres que
  les trois définis plus bas.

## 2. Décisions

| Sujet | Décision |
| --- | --- |
| Portée | Fiches, factures, montants, échéances, historique des coûts, rappels dans l'application |
| Entrée des factures | Dépôt de PDF, lecture par l'IA (Ollama), validation humaine; saisie manuelle toujours possible |
| Association à un fournisseur | Présélection possible (dépôt depuis une fiche), sinon l'IA propose; l'IA peut proposer un nouveau fournisseur |
| Paiement | Marquage manuel; un service en prélèvement automatique est considéré payé à l'échéance |
| Fournisseur et services | Un fournisseur a plusieurs services; les champs se répartissent entre les deux (§5) |
| Stockage des PDF | Système de fichiers existant (SeaweedFS + table `files`) |
| Champs lus par l'IA | Fournisseur et service, total, dates d'émission et d'échéance, période, numéro, consommation, taxes en lignes |
| Historique | Tableau, graphique mensuel, alertes de dépassement |
| Alerte | Facture au-dessus de la moyenne des 6 factures validées précédentes du même service, plus le seuil du service (20 % par défaut); sous 3 factures, comparée au coût prévu |
| Visibilité | Foyer: propriétaire, membre, lecture seule |
| Formation d'un foyer | Lien d'invitation à usage unique, avec expiration, envoyé à la main |
| Traitement des PDF | En arrière-plan, file « à valider » |
| Rattachement | Colonne `household_id` + dépendance FastAPI `Household` |

## 3. Architecture

Backend (`backend/app/`): nouveaux modules de routes dans `api/routes/` branchés dans
`api/router.py`, dépôts dans `repositories/`, modèles et migration Alembic, et un module
de lecture des factures à côté du module de résumé des fichiers.
Frontend (`frontend/src/`): modules `api/`, stores Pinia, vues et routes.

La dépendance `Household` lit le jeton (`Claims`), retrouve le foyer et le rôle, et
remplace le filtre par `sub` pour ces ressources. Les dépôts filtrent toujours sur
`household_id`.

## 4. Foyers

- Une personne appartient à un seul foyer (`household_members.sub` est unique).
- Rôles: `owner` (gère la composition du foyer, l'invitation, le retrait, les rôles et la
  suppression), `member` (gère fournisseurs, services et factures), `viewer` (lecture).
- Invitation: le propriétaire génère un lien portant un jeton aléatoire et choisit le rôle
  offert. La base conserve seulement l'empreinte du jeton. Le lien est à usage unique et
  expire (7 jours). La personne ouvre le lien, se connecte et rejoint le foyer.
- Un membre retiré ou qui part laisse ses données au foyer.
- Sans foyer, l'API répond `409 no_household` et l'interface propose d'en créer un ou de
  rejoindre par lien.

## 5. Modèle de données

Montants en `NUMERIC(12,2)`, en dollars canadiens.

- `households`: id, nom.
- `household_members`: household_id, sub (unique), rôle.
- `household_invitations`: id, household_id, rôle, empreinte du jeton, expiration,
  date d'utilisation, créé par.
- `providers`: id, household_id, nom, site web, téléphone, courriel, notes.
- `services`: id, provider_id, household_id, catégorie, numéro de compte, début de contrat,
  fin de contrat, délai du rappel de renouvellement (jours), coût mensuel prévu,
  prélèvement automatique, seuil d'alerte (%, 20 par défaut), archivé.
- `invoices`: id, household_id, service_id (vide jusqu'à la validation), file_id (vide si
  le fichier est supprimé), statut de traitement (`queued`, `extracting`, `to_validate`,
  `validated`, `failed`), total, date d'échéance (obligatoires à la validation), date
  d'émission, début et fin de période, numéro de facture, quantité et unité de
  consommation, `paid_at`, réponse brute de l'IA.
  Unicité sur (service_id, numéro de facture) quand le numéro est connu.
- `invoice_taxes`: invoice_id, nom, montant.

Le prélèvement automatique n'écrit rien dans `invoices`: l'état « payée » d'une facture en
prélèvement est calculé à la lecture quand l'échéance est passée.

## 6. API (sous `/api`)

Lectures: tous les rôles. Écritures: `member` ou `owner`. Gestion du foyer: `owner`.

- Foyer: lire le foyer et ses membres, créer, supprimer, générer une invitation, rejoindre
  avec un lien, changer le rôle d'un membre, retirer un membre.
- Fournisseurs et services: créer, lire, modifier, archiver. Un service sans facture se
  supprime, sinon on l'archive.
- Factures: déposer un ou plusieurs PDF (fournisseur ou service présélectionné facultatif),
  lister (filtres: statut, service, non payées), valider avec corrections, marquer payée.
- Échéances (`upcoming`): factures à échéance proche, en retard, et renouvellements.
- Coûts d'un service: série mensuelle et factures signalées.

Erreurs: `409 no_household`; `403` rôle insuffisant; `409` doublon de facture (avec
l'identifiant de l'existante); jeton d'invitation invalide, expiré ou déjà utilisé.

## 7. Lecture des factures par l'IA

1. Le dépôt crée un fichier avec le code existant, puis une facture `queued`.
2. Une tâche d'arrière-plan (modèle: le résumé de fichiers) lit le PDF dans SeaweedFS et
   l'envoie à Ollama avec un schéma JSON; elle réutilise l'extraction de texte et de
   pages du module de résumé.
3. La réponse est validée par pydantic. Le fournisseur et le service sont rapprochés par
   nom normalisé avec ceux du foyer; les candidats sont enregistrés pour la validation,
   avec une proposition de création si rien ne correspond.
4. Succès: statut `to_validate`. Échec (Ollama injoignable, réponse invalide): statut
   `failed`, la facture se saisit à la main.
5. Au démarrage, les factures restées à `extracting` passent à `failed`.

La facture n'est jamais enregistrée comme validée sans action humaine.

## 8. Alertes et échéances

- Alerte: pour une facture validée d'un service, référence = moyenne des 6 factures
  validées précédentes du service (au moins 3), sinon le coût prévu du service; signalée
  si le total dépasse la référence de plus que le seuil du service. Calculée à la lecture.
- « À venir »: factures non payées triées par échéance, en retard en premier; renouvellements
  de contrat dont la fin est dans le délai du service.

## 9. Interface

- Modules `api/household.ts`, `api/providers.ts`, `api/invoices.ts` (via `apiRequest`),
  stores Pinia (foyer, fournisseurs, factures), routes ajoutées à `router/index.ts`.
- Vues: accueil Fournisseurs (panneau « À venir », compteur « à valider », liste), fiche
  fournisseur et service (factures, tableau, graphique, alertes), file « À valider »
  (formulaire prérempli, aperçu du PDF, rapprochement proposé), page Foyer (membres, rôles,
  lien d'invitation) et page d'accueil sans foyer.
- Graphique: dépendance déjà installée si possible, sinon tableau seul au départ.
- Le rôle `viewer` voit les écrans sans les boutons d'écriture (l'API refuse de toute façon).

## 10. Tests

Selon `docs/testing.md`.

- Unitaires: calcul des alertes, rapprochement fournisseur et service, validation de la
  réponse de l'IA, droits par rôle, expiration et usage unique des invitations, avec
  `s3_client()`, dépôts et Ollama remplacés par des faux.
- Régression: contrat OpenAPI et bogues épinglés.
- Intégration: nouveaux dépôts contre PostgreSQL.
- Frontend (vitest): stores et formulaires de validation.

## 11. Risques

- Qualité de la lecture par l'IA selon le fournisseur; la validation humaine et la saisie
  manuelle sont le filet de sécurité.
- Les tâches d'arrière-plan ne survivent pas à un redémarrage (voir §7, point 5).
- Le spec est volumineux; le plan d'implémentation devrait suivre l'ordre foyers, puis
  fournisseurs et services, puis factures et lecture par l'IA, puis historique et alertes.
