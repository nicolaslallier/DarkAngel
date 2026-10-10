# Demande de feature pour Infra: service de notifications (courriel + push)

## Contexte

DarkAngel (et probablement d'autres applications de la stack) doit pouvoir prévenir
l'utilisateur d'un événement: par exemple une facture de fournisseur de service qui
arrive à échéance. Aujourd'hui, aucun service d'envoi de courriel ni de notification
push n'existe dans l'Infra, et aucune application n'a de configuration SMTP.

## Besoin

Fournir, au niveau de l'Infra, un point d'envoi partagé que les applications appellent
sur le réseau interne (`infra-net`), sans que chacune embarque sa propre logique SMTP
ou push.

### 1. Courriel

- Un relais SMTP utilisable depuis `infra-net` (hôte, port, authentification éventuelle).
- Un compte ou domaine expéditeur configuré pour que les messages n'atterrissent pas
  dans les indésirables (SPF/DKIM si le relais sort sur Internet).
- Documenter les variables d'environnement à donner aux applications
  (`SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, etc.).

### 2. Notification push (téléphone / navigateur)

- Un service push auto-hébergé (candidat: **ntfy**; alternatives: Gotify, Web Push
  maison) joignable depuis `infra-net`, avec une API HTTP simple (`POST /<sujet>`).
- Les appareils de l'utilisateur doivent pouvoir s'abonner à un sujet, y compris hors
  du réseau local si l'utilisateur le souhaite (à décider: exposition via le proxy
  Infra ou accès par VPN seulement).
- Authentification: au minimum un jeton par application émettrice.

## Hors du périmètre

- Planification des rappels, choix du moment d'envoi et contenu des messages: cela reste
  du ressort de chaque application.
- Gestion des préférences de notification par utilisateur.

## Critères d'acceptation

- [ ] Depuis un conteneur d'`infra-net`, un envoi SMTP de test arrive dans la boîte de l'utilisateur.
- [ ] Depuis un conteneur d'`infra-net`, un `POST` HTTP vers le service push déclenche une
      notification sur le téléphone de l'utilisateur.
- [ ] Les deux services démarrent avec `docker compose` et survivent à un redémarrage.
- [ ] Les variables d'environnement et l'adresse interne de chaque service sont documentées
      dans le README de l'Infra.

## Questions ouvertes pour l'Infra

- Relais SMTP: service tiers (fournisseur d'envoi) ou serveur auto-hébergé?
- Push: ntfy suffit-il, ou faut-il du Web Push standard pour le navigateur?
- Le service push doit-il être exposé à Internet ou rester sur le réseau local / VPN?

## Premier consommateur

DarkAngel, fonction « Fournisseurs de services »: rappels d'échéance de factures.
Cette fonction n'attend pas la demande: ses rappels s'affichent d'abord dans l'application.
