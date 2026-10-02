import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Navigate, useBlocker, useNavigate } from 'react-router';

import { ApiError } from '@/api/client';
import { useCounterSale } from '@/api/queries';
import { useBackButton, useErrorText, useLanguage, useStore } from '@/app/hooks';
import { Icon } from '@/components/Icon';
import {
  Badge,
  BottomBar,
  Button,
  Card,
  Field,
  Page,
  PageHeader,
  Photo,
  Sheet,
  TextArea,
  TextInput,
} from '@/components/ui';
import { money } from '@/lib/format';
import { confirmAction, haptic } from '@/lib/telegram';
import {
  percentOff,
  priceOf,
  totals,
  useCounterDraft,
  type CounterLine,
} from '@/state/counterDraft';
import { toast } from '@/state/toasts';

import { ItemSheet } from './ItemSheet';
import s from './counter.module.css';

const OTHER = 'Other';

/** Step 1: what the customer is buying, at the agreed prices. */
export function CounterItemsScreen() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { storeId, role } = useStore();
  const draft = useCounterDraft();
  const [editing, setEditing] = useState<CounterLine | 'new' | null>(null);
  useBackButton(`/s/${storeId}/orders`);

  // A finished sale left in the draft: start a new one.
  const finished = draft.result !== null;
  const reset = draft.reset;
  useEffect(() => {
    if (finished) reset();
  }, [finished, reset]);

  // Items added: ask before leaving (anywhere except on to Payment).
  const blocker = useBlocker(
    ({ nextLocation }) => draft.lines.length > 0 && !nextLocation.pathname.endsWith('/counter/pay'),
  );
  useEffect(() => {
    if (blocker.state !== 'blocked') return;
    void confirmAction(t('counter.leave')).then((leave) => {
      if (leave) {
        reset();
        blocker.proceed();
      } else blocker.reset();
    });
  }, [blocker, reset, t]);

  return (
    <Page>
      <PageHeader title={t('counter.title')} role={role} />
      <Card label={t('counter.items')}>
        {draft.lines.length === 0 ? (
          <p className={s.empty}>{t('counter.noItems')}</p>
        ) : (
          draft.lines.map((line) => (
            <LineRow key={line.variantId} line={line} onOpen={() => setEditing(line)} />
          ))
        )}
        <Button variant="soft" icon="plus" block onClick={() => setEditing('new')}>
          {t('counter.addItem')}
        </Button>
      </Card>
      {draft.lines.length > 0 && <Totals />}

      <BottomBar>
        <Button
          variant="primary"
          block
          disabled={draft.lines.length === 0}
          onClick={() => navigate(`/s/${storeId}/counter/pay`)}
        >
          {t('counter.continue')}
        </Button>
      </BottomBar>

      {editing && (
        <ItemSheet
          line={editing === 'new' ? undefined : editing}
          onClose={() => setEditing(null)}
          onSave={(line) => {
            if (editing === 'new') draft.addLine(line);
            else draft.updateLine(editing.variantId, line);
            setEditing(null);
          }}
        />
      )}
    </Page>
  );
}

function LineRow({ line, onOpen }: { line: CounterLine; onOpen: () => void }) {
  const { t } = useTranslation();
  const language = useLanguage();
  const removeLine = useCounterDraft((state) => state.removeLine);
  const paid = priceOf(line);
  const off = percentOff(line.listed, paid);
  return (
    <div className={s.line}>
      <button
        type="button"
        className={s.line}
        style={{ padding: 0, border: 'none' }}
        onClick={onOpen}
      >
        <Photo url={line.photoUrl} size={44} alt={line.productName} />
        <span className={s.lineMain}>
          <span className={s.lineName}>{line.productName}</span>
          <span className={s.lineSub}>
            {[line.label, `×${line.quantity}`].filter(Boolean).join(' · ')}
          </span>
        </span>
        <span className={s.lineEnd}>
          <span className={s.lineTotal}>{money(paid * line.quantity, language)}</span>
          {off > 0 ? (
            <span className={s.struck}>{money(line.listed * line.quantity, language)}</span>
          ) : null}
          {line.heldAccepted && <Badge tone="warn">{t('counter.heldShort')}</Badge>}
        </span>
      </button>
      <Button
        variant="link"
        aria-label={`${t('counter.remove')}: ${line.productName}`}
        onClick={() => removeLine(line.variantId)}
      >
        <Icon name="trash" size={18} />
      </Button>
    </div>
  );
}

function Totals() {
  const { t } = useTranslation();
  const language = useLanguage();
  const lines = useCounterDraft((state) => state.lines);
  const sum = totals(lines);
  return (
    <Card>
      <div className={s.totals}>
        {sum.discount > 0 && (
          <>
            <div className={s.totalRow}>
              <span>{t('counter.listedTotal')}</span>
              <span>{money(sum.listed, language)}</span>
            </div>
            <div className={`${s.totalRow} ${s.discount}`}>
              <span>{t('counter.discount')}</span>
              <span>−{money(sum.discount, language)}</span>
            </div>
          </>
        )}
        <div className={`${s.totalRow} ${s.grandTotal}`}>
          <span>{t('counter.toPay')}</span>
          <span>{money(sum.paid, language)}</span>
        </div>
      </div>
    </Card>
  );
}

/** Step 2: how they paid, who they are (optional), and confirm. */
export function CounterPayScreen() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const errorText = useErrorText();
  const language = useLanguage();
  const { storeId, role, store } = useStore();
  const draft = useCounterDraft();
  const sale = useCounterSale(storeId);
  const [heldSheet, setHeldSheet] = useState(false);
  useBackButton(`/s/${storeId}/counter`);

  if (draft.lines.length === 0 && !draft.result) {
    return <Navigate to={`/s/${storeId}/counter`} replace />;
  }

  const methods = store.payment_methods.length > 0 ? store.payment_methods : ['Cash'];
  const isOther = !methods.includes(draft.paymentMethod);
  const otherMissing = isOther && draft.paymentNote.trim() === '';

  const submit = () => {
    sale.mutate(draft.input(), {
      onSuccess: (result) => {
        haptic.success();
        draft.finish(result);
        void navigate(`/s/${storeId}/counter/done`, { replace: true });
      },
      onError: (error) => {
        haptic.error();
        if (error instanceof ApiError && error.code === 'held_by_online_order') {
          setHeldSheet(true);
        } else {
          toast.error(errorText(error));
        }
      },
    });
  };

  return (
    <Page>
      <PageHeader title={t('counter.payTitle')} role={role} />

      <Card label={t('counter.howPaid')}>
        <div className={s.methods} role="radiogroup" aria-label={t('counter.howPaid')}>
          {[...methods, OTHER].map((method) => {
            const on = method === OTHER ? isOther : draft.paymentMethod === method;
            return (
              <button
                key={method}
                type="button"
                role="radio"
                aria-checked={on}
                className={`${s.method} ${on ? s.methodOn : ''}`}
                onClick={() => {
                  haptic.select();
                  draft.set({ paymentMethod: method, paymentNote: '' });
                }}
              >
                {method === OTHER ? t('counter.other') : method}
              </button>
            );
          })}
        </div>
        {isOther && (
          <Field label={t('counter.otherNote')}>
            {(id) => (
              <TextInput
                id={id}
                maxLength={60}
                value={draft.paymentNote}
                onChange={(event) => draft.set({ paymentNote: event.target.value })}
              />
            )}
          </Field>
        )}
      </Card>

      <Card label={t('counter.customer')}>
        <div className={s.twoFields}>
          <Field label={t('counter.customerName')}>
            {(id) => (
              <TextInput
                id={id}
                maxLength={60}
                autoComplete="off"
                value={draft.customerName}
                onChange={(event) => draft.set({ customerName: event.target.value })}
              />
            )}
          </Field>
          <Field label={t('counter.customerPhone')}>
            {(id) => (
              <TextInput
                id={id}
                type="tel"
                inputMode="tel"
                maxLength={20}
                autoComplete="off"
                value={draft.customerPhone}
                onChange={(event) => draft.set({ customerPhone: event.target.value })}
              />
            )}
          </Field>
        </div>
        <Field label={t('counter.note')}>
          {(id) => (
            <TextArea
              id={id}
              rows={2}
              maxLength={300}
              placeholder={t('counter.notePlaceholder')}
              value={draft.note}
              onChange={(event) => draft.set({ note: event.target.value })}
            />
          )}
        </Field>
      </Card>

      <Totals />

      <BottomBar>
        <Button
          variant="primary"
          block
          busy={sale.isPending}
          disabled={otherMissing}
          onClick={submit}
        >
          {t('counter.confirm')} · {money(totals(draft.lines).paid, language)}
        </Button>
      </BottomBar>

      <Sheet
        open={heldSheet}
        onClose={() => setHeldSheet(false)}
        label={t('counter.heldTitle')}
        footer={
          <div className={s.doneButtons}>
            <Button
              variant="dangerOutline"
              block
              onClick={() => {
                setHeldSheet(false);
                draft.acceptHolds();
                submit();
              }}
            >
              {t('counter.sellAnyway')}
            </Button>
            <Button block onClick={() => setHeldSheet(false)}>
              {t('counter.cancel')}
            </Button>
          </div>
        }
      >
        <div className={s.held}>
          <Icon name="warning" size={22} />
          <div>
            <p className={s.heldTitle}>{t('counter.heldTitle')}</p>
            <p className={s.heldBody} style={{ marginBottom: 0 }}>
              {sale.error instanceof Error ? sale.error.message : ''}
            </p>
          </div>
        </div>
      </Sheet>
    </Page>
  );
}

/** Step 3: saved. */
export function CounterDoneScreen() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const language = useLanguage();
  const { storeId, role } = useStore();
  const result = useCounterDraft((state) => state.result);
  useBackButton(`/s/${storeId}/orders`);

  if (!result) return <Navigate to={`/s/${storeId}/orders`} replace />;

  const discount = Number(result.discount);
  return (
    <Page>
      <PageHeader title={t('counter.title')} role={role} />
      <div className={s.done}>
        <div className={s.doneIcon}>
          <Icon name="check" size={40} />
        </div>
        <h1 className={s.doneTitle}>{t('counter.doneTitle')}</h1>
        <p className={s.doneBody}>{t('counter.doneBody')}</p>
      </div>
      <Card>
        <div className={s.totalRow}>
          <span>{t('counter.saleNumber', { number: result.number })}</span>
          <Badge tone="success">{t('orders.paid')}</Badge>
        </div>
        {discount > 0 && (
          <div className={`${s.totalRow} ${s.discount}`}>
            <span>{t('counter.discount')}</span>
            <span>−{money(result.discount, language)}</span>
          </div>
        )}
        <div className={`${s.totalRow} ${s.grandTotal}`}>
          <span>{t('counter.toPay')}</span>
          <span>{money(result.total, language)}</span>
        </div>
      </Card>
      {result.held_orders.length > 0 && (
        <div className={s.held} role="status">
          <Icon name="warning" size={22} />
          <p className={s.heldBody} style={{ margin: 0 }}>
            {t('counter.callCustomers', {
              orders: result.held_orders.map((n) => `#${n}`).join(', '),
            })}
          </p>
        </div>
      )}
      <BottomBar>
        <div className={s.doneButtons} style={{ width: '100%' }}>
          <Button
            variant="primary"
            block
            onClick={() => {
              void navigate(`/s/${storeId}/counter`, { replace: true });
            }}
          >
            {t('counter.newSale')}
          </Button>
          <Button
            block
            onClick={() => {
              void navigate(`/s/${storeId}/orders`, { replace: true });
            }}
          >
            {t('counter.backToOrders')}
          </Button>
        </div>
      </BottomBar>
    </Page>
  );
}
