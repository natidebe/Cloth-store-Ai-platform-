import {
  Activity,
  BarChart3,
  Box,
  Check,
  EyeOff,
  Gift,
  Lock,
  MessageCircle,
  MessageCircleQuestion,
  Moon,
  Play,
  Plus,
  Send,
  Shirt,
  ShieldCheck,
  Smartphone,
  SprayCan,
  Store,
  Users,
  type LucideIcon,
} from 'lucide-react';
import { useState, type ReactNode } from 'react';

import { site, startLink, supportLink } from '@/config';
import { useContent } from '@/content';
import ui from '@/styles/ui.module.css';

import { ConnectMock, CreateMock, DashboardMock, PostMock, StockMock } from './Mocks';
import s from './sections.module.css';
import { HeroShowcase } from './Showcase';

function Section({
  id,
  grey,
  children,
  label,
}: {
  id?: string;
  grey?: boolean;
  children: ReactNode;
  label?: string;
}) {
  return (
    <section id={id} className={`${ui.section} ${grey ? ui.grey : ''}`} aria-label={label}>
      <div className={ui.container}>{children}</div>
    </section>
  );
}

function Tile({ icon: Icon, tone }: { icon: LucideIcon; tone: string | undefined }) {
  return (
    <span className={`${ui.iconTile} ${tone}`} aria-hidden="true">
      <Icon size={22} strokeWidth={1.9} />
    </span>
  );
}

const supportHandle = (
  <a className={ui.link} href={supportLink}>
    @{site.support}
  </a>
);

// --- Hero ------------------------------------------------------------------------

export function Hero() {
  const t = useContent().hero;
  return (
    <section className={s.hero}>
      <div className={`${ui.container} ${s.heroGrid}`}>
        <div>
          <h1 className={s.h1}>{t.title}</h1>
          <p className={s.heroBody}>{t.body}</p>
          <div className={s.heroButtons}>
            <a className={`${ui.button} ${ui.primary} ${ui.big}`} href={startLink}>
              <Send size={20} aria-hidden="true" />
              {t.start}
            </a>
            <a className={`${ui.button} ${ui.soft} ${ui.big}`} href="#how">
              {t.how}
            </a>
          </div>
          <ul className={s.checks}>
            {t.checks.map((check) => (
              <li key={check}>
                <Check size={16} strokeWidth={2.6} aria-hidden="true" />
                {check}
              </li>
            ))}
          </ul>
        </div>
        <HeroShowcase />
      </div>
    </section>
  );
}

// --- The problem -----------------------------------------------------------------

const PROBLEM_TILES: [LucideIcon, string | undefined][] = [
  [MessageCircleQuestion, ui.tileBlue],
  [Moon, ui.tileYellow],
  [Box, ui.tileGreen],
  [EyeOff, ui.tileRed],
];

export function Problems() {
  const t = useContent().problems;
  return (
    <Section grey>
      <h2 className={ui.h2}>{t.title}</h2>
      <div className={s.four}>
        {t.items.map((item, index) => {
          const [icon, tone] = PROBLEM_TILES[index] ?? [Box, ui.tileBlue];
          return (
            <article key={item.title} className={s.card}>
              <Tile icon={icon} tone={tone} />
              <h3 className={s.cardTitle}>{item.title}</h3>
              <p className={s.cardBody}>{item.body}</p>
            </article>
          );
        })}
      </div>
    </Section>
  );
}

// --- How it works ----------------------------------------------------------------

const STEP_MOCKS = [CreateMock, ConnectMock, StockMock, PostMock];

export function Steps() {
  const t = useContent().steps;
  return (
    <Section id="how">
      <h2 className={ui.h2}>{t.title}</h2>
      <ol className={s.steps}>
        {t.items.map((step, index) => {
          const Mock = STEP_MOCKS[index] ?? CreateMock;
          return (
            <li key={step.title} className={s.step}>
              <div className={`${s.stepPanel} ${index === 3 ? s.stepPanelChat : ''}`}>
                <Mock />
              </div>
              <div className={s.stepText}>
                <span className={s.stepNumber} aria-hidden="true">
                  {index + 1}
                </span>
                <div>
                  <h3 className={s.stepTitle}>{step.title}</h3>
                  <p className={s.cardBody}>{step.body.replace('{bot}', site.platformBot)}</p>
                </div>
              </div>
            </li>
          );
        })}
      </ol>
    </Section>
  );
}

// --- Features ----------------------------------------------------------------------

const GROUP_TILES: [LucideIcon, string | undefined][] = [
  [Users, ui.tileBlue],
  [MessageCircle, ui.tileGreen],
  [BarChart3, ui.tileYellow],
];

export function Features() {
  const t = useContent().features;
  return (
    <Section id="features" grey>
      <h2 className={ui.h2}>{t.title}</h2>
      <div className={s.groups}>
        {t.groups.map((group, index) => {
          const [icon, tone] = GROUP_TILES[index] ?? [Users, ui.tileBlue];
          return (
            <article key={group.title} className={s.group}>
              <div className={s.groupHead}>
                <Tile icon={icon} tone={tone} />
                <h3 className={s.groupTitle}>{group.title}</h3>
                {index === 2 && <DashboardMock />}
              </div>
              <div className={s.groupItems}>
                {group.items.map((item) => (
                  <div key={item.title}>
                    <h4 className={s.itemTitle}>{item.title}</h4>
                    <p className={s.itemBody}>{item.body}</p>
                  </div>
                ))}
              </div>
            </article>
          );
        })}
        <aside className={s.control}>
          <span className={s.controlIcon} aria-hidden="true">
            <ShieldCheck size={24} />
          </span>
          <div>
            <h3 className={s.controlTitle}>{t.control.title}</h3>
            <p className={s.controlBody}>
              {t.control.before}
              <strong>{t.control.strong}</strong>
              {t.control.after}
            </p>
          </div>
        </aside>
      </div>
    </Section>
  );
}

// --- Demo -------------------------------------------------------------------------

/** Shown once there is a demo channel or a video (site.demoUrl / site.videoUrl). */
export function Demo() {
  const t = useContent().demo;
  const [playing, setPlaying] = useState(false);
  if (!site.demoUrl && !site.videoUrl) return null;
  return (
    <Section>
      <div className={s.demo}>
        <div>
          <h2 className={`${ui.h2} ${s.demoTitle}`}>{t.title}</h2>
          <p className={s.demoBody}>{t.body}</p>
          {site.demoUrl && (
            <a className={`${ui.button} ${ui.primary} ${ui.big}`} href={site.demoUrl}>
              <Store size={20} aria-hidden="true" />
              {t.open}
            </a>
          )}
        </div>
        <div className={s.video}>
          {playing && site.videoUrl ? (
            <video className={s.videoPlayer} src={site.videoUrl} controls autoPlay playsInline />
          ) : (
            <>
              <button
                type="button"
                className={s.play}
                onClick={() => setPlaying(true)}
                disabled={!site.videoUrl}
                aria-label={t.video}
              >
                <Play size={30} fill="currentColor" aria-hidden="true" />
              </button>
              <p className={s.videoTitle}>{site.videoUrl ? t.video : t.videoSoon}</p>
              <p className={s.videoFlow}>{t.videoFlow}</p>
            </>
          )}
        </div>
      </div>
    </Section>
  );
}

// --- Who it's for ------------------------------------------------------------------

const AUDIENCE_ICONS = [Shirt, SprayCan, Smartphone, Gift];

export function Audience() {
  const t = useContent().audience;
  return (
    <Section grey>
      <h2 className={ui.h2}>{t.title}</h2>
      <div className={s.four}>
        {t.items.map((item, index) => {
          const Icon = AUDIENCE_ICONS[index] ?? Store;
          return (
            <article key={item.title} className={`${s.card} ${s.cardSmall}`}>
              <Icon size={26} strokeWidth={1.8} className={s.audienceIcon} aria-hidden="true" />
              <h3 className={s.audienceTitle}>{item.title}</h3>
              <p className={s.cardBody}>{item.body}</p>
            </article>
          );
        })}
      </div>
      <p className={s.other}>
        {t.other} {supportHandle}
      </p>
    </Section>
  );
}

// --- Pricing ------------------------------------------------------------------------

export function Pricing() {
  const t = useContent().pricing;
  return (
    <Section id="pricing">
      <h2 className={ui.h2}>{t.title}</h2>
      <div className={s.plans}>
        {t.plans.map((plan, index) => {
          const popular = index === 1;
          return (
            <article key={plan.name} className={`${s.plan} ${popular ? s.planPopular : ''}`}>
              {popular && <span className={s.badge}>{t.popular}</span>}
              <h3 className={s.planName}>{plan.name}</h3>
              <p className={s.price}>
                <span className={s.priceNumber}>
                  {plan.price} {t.currency}
                </span>{' '}
                <span className={s.priceUnit}>{plan.unit}</span>
              </p>
              <p className={s.planFor}>{plan.for}</p>
              <p className={s.planIncludes}>{plan.includes}</p>
              <a
                className={`${ui.button} ${popular ? ui.primary : ui.soft} ${ui.block}`}
                href={startLink}
              >
                {t.start}
              </a>
            </article>
          );
        })}
      </div>
      <div className={s.notes}>
        {t.notes.map((note, index) => (
          <p key={note.label} className={`${s.note} ${index === 0 ? s.noteFounding : ''}`}>
            <strong>{note.label}</strong> {note.text}
          </p>
        ))}
      </div>
    </Section>
  );
}

// --- Built in Ethiopia ----------------------------------------------------------------

export function Trust() {
  const t = useContent().trust;
  return (
    <Section grey>
      <div className={s.trust}>
        <div>
          <h2 className={ui.h2}>{t.title}</h2>
          <ul className={s.people}>
            {t.people.map((person) => (
              <li key={person.name} className={s.person}>
                <span className={s.personPhoto} aria-hidden="true">
                  {person.name.slice(0, 1)}
                </span>
                <span>
                  <span className={s.personName}>{person.name}</span>
                  <span className={s.personRole}>{person.role}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
        <div className={s.promises}>
          <PromiseCard icon={Lock} tone={s.toneGreen} title={t.data.title}>
            {t.data.body}
          </PromiseCard>
          <PromiseCard icon={Activity} tone={s.toneBlue} title={t.reliable.title}>
            {t.reliable.body}
            {site.statusUrl && (
              <>
                {' '}
                {t.reliable.status}{' '}
                <a className={ui.link} href={site.statusUrl}>
                  {t.reliable.link}
                </a>
              </>
            )}
          </PromiseCard>
          <PromiseCard icon={MessageCircle} tone={s.toneAmber} title={t.person.title}>
            {supportHandle} {t.person.after}
          </PromiseCard>
        </div>
      </div>
    </Section>
  );
}

function PromiseCard({
  icon: Icon,
  tone,
  title,
  children,
}: {
  icon: LucideIcon;
  tone: string | undefined;
  title: string;
  children: ReactNode;
}) {
  return (
    <article className={s.promise}>
      <Icon size={22} className={tone} aria-hidden="true" />
      <div>
        <h3 className={s.promiseTitle}>{title}</h3>
        <p className={s.cardBody}>{children}</p>
      </div>
    </article>
  );
}

// --- FAQ ----------------------------------------------------------------------------

export function Faq() {
  const t = useContent().faq;
  return (
    <Section id="faq">
      <div className={s.faq}>
        <h2 className={ui.h2}>{t.title}</h2>
        {t.items.map((item) => (
          <details key={item.q} className={s.question}>
            <summary>
              {item.q}
              <Plus size={20} className={s.plus} aria-hidden="true" />
            </summary>
            <p className={s.answer}>{item.a}</p>
          </details>
        ))}
      </div>
    </Section>
  );
}

// --- Final call to action ------------------------------------------------------------

export function FinalCta() {
  const t = useContent().cta;
  return (
    <Section grey>
      <div className={s.cta}>
        <h2 className={s.ctaTitle}>{t.title}</h2>
        <p className={s.ctaBody}>{t.body}</p>
        <div className={s.ctaButtons}>
          <a className={`${ui.button} ${ui.white} ${ui.big}`} href={startLink}>
            {t.start}
          </a>
          <a className={`${ui.button} ${ui.outlineWhite} ${ui.big}`} href={supportLink}>
            {t.talk}
          </a>
        </div>
      </div>
    </Section>
  );
}
