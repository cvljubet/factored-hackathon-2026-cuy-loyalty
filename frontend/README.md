# Frontend

Cuy Loyalty user interface, built with [Vite](https://vite.dev), React, TypeScript and [Tailwind CSS](https://tailwindcss.com) v4 (via the `@tailwindcss/vite` plugin; there is no `tailwind.config.js`).

## Requirements

- Node.js 20.19+ or 22.12+ (developed with Node 24)
- npm

## Commands

Run from this `frontend/` directory:

```sh
npm install       # install dependencies (uses package-lock.json)
npm run dev       # start the dev server at http://localhost:5173
npm run build     # type-check and build to dist/
npm run preview   # serve the production build locally
npm run lint      # lint with oxlint
npm test          # run unit tests once (Vitest); npm run test:watch to re-run on save
```

## Authentication (Cognito)

Sign-in uses the Cognito user pool from `infrastructure/terraform` through
[Amplify Auth](https://docs.amplify.aws/react/build-a-backend/auth/) (SRP, email +
password). Amplify keeps and refreshes the tokens; the app never stores passwords or JWTs.

1. Copy `.env.example` to `.env.local` (git-ignored) and fill it in from
   `terraform -chdir=infrastructure/terraform/envs/dev output`:

   ```sh
   VITE_AWS_REGION=us-east-2
   VITE_COGNITO_USER_POOL_ID=<cognito_user_pool_id>
   VITE_COGNITO_CLIENT_ID=<cognito_client_id>
   ```

   These values are not secret. The app refuses to start if any is missing.
2. Create a user as described in `infrastructure/terraform/README.md`
   (give it a permanent password, `given_name` and `custom:customer_id`).
3. `npm run dev`, open http://localhost:5173/login and sign in.

`/chat` is protected: without a session it redirects to `/login`. The session
survives a page refresh, and the logout button in the header ends it.

Code lives in `src/auth/`: `AuthProvider` holds the auth state, `ProtectedRoute`
guards routes, and `authService.ts` is the only file that talks to Amplify (tests
use a fake service instead).
