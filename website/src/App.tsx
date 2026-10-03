import { LangContext, type Lang } from '@/content';

import { Footer, Header } from './components/Layout';
import {
  Audience,
  Demo,
  Faq,
  Features,
  FinalCta,
  Hero,
  Pricing,
  Problems,
  Steps,
  Trust,
} from './components/Sections';

/** The whole page, in one language (design: website/design/Landing page). */
export function App({ lang }: { lang: Lang }) {
  return (
    <LangContext.Provider value={lang}>
      <Header />
      <main id="main">
        <Hero />
        <Problems />
        <Steps />
        <Features />
        <Demo />
        <Audience />
        <Pricing />
        <Trust />
        <Faq />
        <FinalCta />
      </main>
      <Footer />
    </LangContext.Provider>
  );
}
