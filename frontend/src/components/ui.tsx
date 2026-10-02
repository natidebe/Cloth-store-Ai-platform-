import {
  useEffect,
  useId,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type TextareaHTMLAttributes,
} from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';

import type { Role } from '@/api/types';
import { haptic } from '@/lib/telegram';
import { useToasts } from '@/state/toasts';

import { Icon, type IconName } from './Icon';
import s from './ui.module.css';

const cx = (...names: (string | false | null | undefined)[]) => names.filter(Boolean).join(' ');

// --- Page ------------------------------------------------------------------------

export function Page({ children, bar = true }: { children: ReactNode; bar?: boolean }) {
  return <main className={cx(s.page, !bar && s.pageNoBar)}>{children}</main>;
}

export function PageHeader({
  title,
  subtitle,
  role,
}: {
  title: string;
  subtitle?: string;
  role?: Role;
}) {
  return (
    <header className={s.header}>
      <div>
        <h1 className={s.title}>{title}</h1>
        {subtitle && <p className={s.subtitle}>{subtitle}</p>}
      </div>
      {role && <RoleBadge role={role} />}
    </header>
  );
}

export function RoleBadge({ role }: { role: Role }) {
  const { t } = useTranslation();
  return (
    <span className={cx(s.roleBadge, role === 'staff' && s.roleStaff)}>
      {role === 'owner' ? t('common.owner') : t('common.staff')}
    </span>
  );
}

export function Card({
  children,
  flush,
  label,
}: {
  children: ReactNode;
  flush?: boolean;
  label?: string;
}) {
  return (
    <section className={cx(s.card, flush && s.cardFlush)}>
      {label && <h2 className={s.sectionLabel}>{label}</h2>}
      {children}
    </section>
  );
}

// --- Buttons -----------------------------------------------------------------------

type Variant = 'primary' | 'outline' | 'dangerOutline' | 'soft' | 'link';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  block?: boolean;
  icon?: IconName;
  busy?: boolean;
}

export function Button({
  variant = 'outline',
  block,
  icon,
  busy,
  children,
  className,
  onClick,
  ...rest
}: ButtonProps) {
  return (
    <button
      type="button"
      className={cx(s.button, s[variant], block && s.block, className)}
      aria-busy={busy || undefined}
      onClick={(event) => {
        haptic.tap();
        onClick?.(event);
      }}
      {...rest}
      disabled={rest.disabled || busy}
    >
      {icon && <Icon name={icon} size={18} />}
      {children}
    </button>
  );
}

/** The big button fixed at the bottom (design: "Add product", "Save"). */
export function BottomBar({ children }: { children: ReactNode }) {
  return (
    <div className={s.bottomBar}>
      <div className={s.bottomBarInner}>{children}</div>
    </div>
  );
}

// --- Fields -------------------------------------------------------------------------

interface FieldProps {
  label?: string;
  hint?: string;
  error?: string;
  children: (id: string) => ReactNode;
}

export function Field({ label, hint, error, children }: FieldProps) {
  const id = useId();
  return (
    <div className={s.field}>
      {label && (
        <label className={s.label} htmlFor={id}>
          {label}
        </label>
      )}
      {children(id)}
      {error ? (
        <p className={s.error} role="alert">
          {error}
        </p>
      ) : (
        hint && <p className={s.hint}>{hint}</p>
      )}
    </div>
  );
}

interface TextInputProps extends InputHTMLAttributes<HTMLInputElement> {
  invalid?: boolean;
  locked?: boolean;
}

export function TextInput({ invalid, locked, className, ...rest }: TextInputProps) {
  const input = (
    <input className={cx(s.input, invalid && s.invalid, className)} disabled={locked} {...rest} />
  );
  if (!locked) return input;
  return (
    <div className={s.inputWrap}>
      {input}
      <Icon name="lock" size={18} className={s.inputIcon} />
    </div>
  );
}

interface TextAreaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  invalid?: boolean;
  counter?: { value: number; max: number };
}

export function TextArea({ invalid, counter, className, ...rest }: TextAreaProps) {
  return (
    <>
      <textarea className={cx(s.textarea, invalid && s.invalid, className)} {...rest} />
      {counter && (
        <p className={s.counter}>
          {counter.value} / {counter.max}
        </p>
      )}
    </>
  );
}

// --- Stepper ------------------------------------------------------------------------

interface StepperProps {
  value: number;
  onChange: (value: number) => void;
  label: string;
  min?: number;
  max?: number;
}

export function Stepper({ value, onChange, label, min = 0, max = 100_000 }: StepperProps) {
  const set = (next: number) => {
    const clamped = Math.max(min, Math.min(max, Number.isFinite(next) ? Math.round(next) : min));
    if (clamped !== value) haptic.select();
    onChange(clamped);
  };
  return (
    <div className={s.stepper} role="group" aria-label={label}>
      <button
        type="button"
        className={s.stepperButton}
        onClick={() => set(value - 1)}
        disabled={value <= min}
        aria-label={`${label} −1`}
      >
        <Icon name="minus" size={18} />
      </button>
      <input
        className={s.stepperValue}
        type="number"
        inputMode="numeric"
        value={value}
        aria-label={label}
        onChange={(event) => set(Number.parseInt(event.target.value || '0', 10))}
        onFocus={(event) => event.target.select()}
      />
      <button
        type="button"
        className={s.stepperButton}
        onClick={() => set(value + 1)}
        disabled={value >= max}
        aria-label={`${label} +1`}
      >
        <Icon name="plus" size={18} />
      </button>
    </div>
  );
}

// --- Badges, chips, segmented tabs --------------------------------------------------

export function Badge({
  tone,
  children,
}: {
  tone: 'warn' | 'danger' | 'success' | 'neutral' | 'info';
  children: ReactNode;
}) {
  return <span className={cx(s.badge, s[tone])}>{children}</span>;
}

export function Chips<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div className={s.chips} role="radiogroup" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          className={cx(s.chip, option.value === value && s.chipActive)}
          onClick={() => {
            haptic.select();
            onChange(option.value);
          }}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div className={s.segment} role="tablist" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="tab"
          aria-selected={option.value === value}
          className={cx(s.segmentItem, option.value === value && s.segmentActive)}
          onClick={() => {
            haptic.select();
            onChange(option.value);
          }}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

// --- Rows -----------------------------------------------------------------------------

interface RowProps {
  icon?: IconName;
  title: string;
  subtitle?: string;
  end?: ReactNode;
  muted?: boolean;
  onClick?: () => void;
  disabled?: boolean;
}

export function Row({ icon, title, subtitle, end, muted, onClick, disabled }: RowProps) {
  const content = (
    <>
      {icon && (
        <span className={cx(s.rowIcon, muted && s.rowIconMuted)}>
          <Icon name={icon} />
        </span>
      )}
      <span className={s.rowMain}>
        <span className={s.rowTitle} style={muted ? { color: 'var(--text-muted)' } : undefined}>
          {title}
        </span>
        {subtitle && (
          <span className={s.rowSub} style={{ display: 'block' }}>
            {subtitle}
          </span>
        )}
      </span>
      {end !== undefined && <span className={s.rowEnd}>{end}</span>}
    </>
  );
  if (!onClick) return <div className={s.row}>{content}</div>;
  return (
    <button type="button" className={s.row} onClick={onClick} disabled={disabled}>
      {content}
    </button>
  );
}

export function Notice({
  tone,
  icon,
  children,
}: {
  tone: 'warn' | 'danger' | 'info';
  icon?: IconName;
  children: ReactNode;
}) {
  return (
    <div className={cx(s.notice, s[tone])} role="status">
      {icon && <Icon name={icon} size={20} />}
      <span>{children}</span>
    </div>
  );
}

export function Steps({ items }: { items: ReactNode[] }) {
  return (
    <ol className={s.steps}>
      {items.map((item, index) => (
        <li key={index} className={s.step}>
          <span className={s.stepNumber}>{index + 1}</span>
          <span style={{ paddingTop: 3 }}>{item}</span>
        </li>
      ))}
    </ol>
  );
}

// --- Sheet ------------------------------------------------------------------------------

interface SheetProps {
  open: boolean;
  onClose: () => void;
  label: string;
  children: ReactNode;
  footer?: ReactNode;
}

/** A bottom sheet (design: Quick stock, Plan). Escape or a tap outside closes it. */
export function Sheet({ open, onClose, label, children, footer }: SheetProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKey);
      document.body.style.overflow = overflow;
    };
  }, [open, onClose]);

  if (!open) return null;
  return createPortal(
    <>
      <div className={s.overlay} onClick={onClose} aria-hidden="true" />
      <div className={s.sheet} role="dialog" aria-modal="true" aria-label={label}>
        <div className={s.sheetHandle} />
        <div className={s.sheetBody}>{children}</div>
        {footer && <div className={s.sheetFooter}>{footer}</div>}
      </div>
    </>,
    document.body,
  );
}

// --- Empty, status, loading ---------------------------------------------------------------

interface CenteredProps {
  icon: IconName;
  title: string;
  body?: string;
  code?: string;
  tone?: 'info' | 'warn';
  action?: ReactNode;
}

export function Centered({ icon, title, body, code, tone = 'info', action }: CenteredProps) {
  return (
    <div className={s.centered}>
      <span className={cx(s.bigIcon, tone === 'warn' && s.bigIconWarn)}>
        <Icon name={icon} size={34} />
      </span>
      <h2 className={s.centeredTitle}>{title}</h2>
      {body && <p className={s.centeredBody}>{body}</p>}
      {code && <p className={s.code}>{code}</p>}
      {action}
    </div>
  );
}

export function SkeletonList({ rows = 6 }: { rows?: number }) {
  const { t } = useTranslation();
  return (
    <div aria-busy="true" aria-label={t('common.loading')}>
      <Card flush>
        {Array.from({ length: rows }, (_, index) => (
          <div key={index} className={s.skeletonRow}>
            <span className={s.skeleton} style={{ width: 56, height: 56, borderRadius: 12 }} />
            <span style={{ flex: 1 }}>
              <span
                className={s.skeleton}
                style={{ display: 'block', width: '70%', height: 14, marginBottom: 10 }}
              />
              <span className={s.skeleton} style={{ display: 'block', width: '45%', height: 12 }} />
            </span>
            <span className={s.skeleton} style={{ width: 32, height: 20 }} />
          </div>
        ))}
      </Card>
      <p className={s.loadingText}>{t('common.loading')}</p>
    </div>
  );
}

export function Toggle({
  on,
  onChange,
  label,
}: {
  on: boolean;
  onChange: (on: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      className={cx(s.toggle, on && s.toggleOn)}
      onClick={() => {
        haptic.select();
        onChange(!on);
      }}
    />
  );
}

export function Photo({ url, size, alt }: { url: string | null; size: number; alt: string }) {
  const { t } = useTranslation();
  const style = { width: size, height: size, borderRadius: size > 80 ? 14 : 12 };
  if (!url) {
    return (
      <span className={s.photoPlaceholder} style={style}>
        {size > 80 ? t('product.photo') : null}
      </span>
    );
  }
  return <img className={s.photo} src={url} alt={alt} style={style} loading="lazy" />;
}

export function Toasts() {
  const toasts = useToasts((state) => state.toasts);
  return (
    <div className={s.toasts} aria-live="polite">
      {toasts.map((item) => (
        <div key={item.id} className={cx(s.toast, item.tone === 'error' && s.toastError)}>
          {item.text}
        </div>
      ))}
    </div>
  );
}
