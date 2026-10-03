import { StrictMode } from 'react';
import { createRoot, hydrateRoot } from 'react-dom/client';

import { langOfPath } from '@/content';

import { App } from './App';
import './styles/global.css';

const root = document.getElementById('root') as HTMLElement;
const app = (
  <StrictMode>
    <App lang={langOfPath(window.location.pathname)} />
  </StrictMode>
);

// Built pages already contain the HTML (scripts/prerender.mjs): React takes it
// over. In development (`npm run dev`) the page is empty: React draws it.
if (root.firstElementChild) hydrateRoot(root, app);
else createRoot(root).render(app);
