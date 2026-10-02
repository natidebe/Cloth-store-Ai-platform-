import { zodResolver } from '@hookform/resolvers/zod';
import { useEffect, useState } from 'react';
import { Controller, useFieldArray, useForm, type UseFieldArrayReturn } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { Navigate } from 'react-router';
import { z } from 'zod';

import { useSaveSettings, useSettings } from '@/api/queries';
import { WEEK_DAYS, type OpeningWeek, type StoreSettings } from '@/api/types';
import { ErrorScreen } from '@/app/AccessScreens';
import { useBackButton, useErrorText, useLanguage, useStore } from '@/app/hooks';
import { inputClass } from '@/components/classes';
import { Icon } from '@/components/Icon';
import {
  BottomBar,
  Button,
  Card,
  Field,
  Page,
  PageHeader,
  Row,
  Sheet,
  SkeletonList,
  TextArea,
  TextInput,
  Toggle,
} from '@/components/ui';
import { money } from '@/lib/format';
import { haptic } from '@/lib/telegram';
import { toast } from '@/state/toasts';

import s from './settings.module.css';

const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;
const DEFAULT_DAY = { open: true, from: '08:30', to: '19:00' };

const account = z.object({
  name: z.string().trim().min(1).max(40),
  number: z.string().trim().min(1).max(60),
  holder: z.string().trim().max(60).optional(),
});
const area = z.object({
  area: z.string().trim().min(1).max(60),
  fee: z.number().min(0).max(100_000),
});
const day = z.object({ open: z.boolean(), from: z.string(), to: z.string() });

const formSchema = z.object({
  payment_accounts: z.array(account).max(10),
  delivery_areas: z.array(area).max(30),
  week: z.object(
    Object.fromEntries(WEEK_DAYS.map((d) => [d, day])) as Record<
      (typeof WEEK_DAYS)[number],
      typeof day
    >,
  ),
  location: z.string().max(1000),
  pickup_instructions: z.string().max(1000),
  return_policy: z.string().max(1000),
});

type FormValues = z.infer<typeof formSchema>;

function toForm(settings: StoreSettings): FormValues {
  const week = settings.opening_week;
  return {
    payment_accounts: settings.payment_accounts.map((a) => ({
      name: a.name,
      number: a.number,
      holder: a.holder ?? '',
    })),
    delivery_areas: settings.delivery_areas.map((a) => ({ area: a.area, fee: Number(a.fee) })),
    week: Object.fromEntries(
      WEEK_DAYS.map((d) => {
        const saved = week?.[d];
        if (!week)
          return [d, d === 'sun' ? { open: false, from: '08:30', to: '19:00' } : DEFAULT_DAY];
        return [
          d,
          { open: Boolean(saved?.open), from: saved?.from ?? '08:30', to: saved?.to ?? '19:00' },
        ];
      }),
    ) as FormValues['week'],
    location: settings.location ?? '',
    pickup_instructions: settings.pickup_instructions ?? '',
    return_policy: settings.return_policy ?? '',
  };
}

/** Design: "Profile payment and delivery" + "Profile location, hours, policies". */
export function StoreProfileScreen() {
  const { storeId, isOwner } = useStore();
  const settings = useSettings(storeId, isOwner);
  useBackButton(`/s/${storeId}/settings`);

  if (!isOwner) return <Navigate to={`/s/${storeId}/settings`} replace />;
  if (settings.isPending) {
    return (
      <Page>
        <SkeletonList rows={4} />
      </Page>
    );
  }
  if (settings.isError)
    return <ErrorScreen error={settings.error} onRetry={() => void settings.refetch()} />;
  return <ProfileForm settings={settings.data} />;
}

function ProfileForm({ settings }: { settings: StoreSettings }) {
  const { t } = useTranslation();
  const errorText = useErrorText();
  const { storeId, role } = useStore();
  const save = useSaveSettings(storeId);
  const { control, register, handleSubmit, reset, formState } = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: toForm(settings),
  });
  useEffect(() => reset(toForm(settings), { keepDirtyValues: true }), [settings, reset]);
  const accounts = useFieldArray({ control, name: 'payment_accounts' });
  const areas = useFieldArray({ control, name: 'delivery_areas' });

  const onSubmit = handleSubmit((values) => {
    for (const d of WEEK_DAYS) {
      const hours = values.week[d];
      if (hours.open && !(TIME.test(hours.from) && TIME.test(hours.to) && hours.to > hours.from)) {
        toast.error(t('profile.hoursInvalid', { day: t(`profile.days.${d}`) }));
        return;
      }
    }
    const week = Object.fromEntries(
      WEEK_DAYS.map((d) => {
        const hours = values.week[d];
        return [d, hours.open ? { open: true, from: hours.from, to: hours.to } : { open: false }];
      }),
    ) as OpeningWeek;
    save.mutate(
      {
        payment_accounts: values.payment_accounts.map((a) => ({
          name: a.name,
          number: a.number,
          holder: a.holder || null,
        })),
        delivery_areas: values.delivery_areas,
        opening_week: week,
        location: values.location,
        pickup_instructions: values.pickup_instructions,
        return_policy: values.return_policy,
      },
      {
        onSuccess: () => {
          haptic.success();
          toast.info(t('common.saved'));
        },
        onError: (error) => {
          haptic.error();
          toast.error(errorText(error));
        },
      },
    );
  });

  return (
    <Page>
      <PageHeader title={t('profile.title')} role={role} />
      <form onSubmit={onSubmit} noValidate>
        <AccountsCard accounts={accounts} />
        <AreasCard areas={areas} />

        <Card label={t('profile.location')}>
          <Field label={t('profile.address')}>
            {(id) => (
              <TextInput
                id={id}
                placeholder={t('profile.addressPlaceholder')}
                {...register('location')}
              />
            )}
          </Field>
        </Card>

        <Card label={t('profile.openingHours')}>
          {WEEK_DAYS.map((d) => (
            <Controller
              key={d}
              control={control}
              name={`week.${d}`}
              render={({ field }) => (
                <div className={s.dayRow}>
                  <span className={s.dayName}>{t(`profile.days.${d}`)}</span>
                  <Toggle
                    on={field.value.open}
                    label={t(`profile.days.${d}`)}
                    onChange={(open) => field.onChange({ ...field.value, open })}
                  />
                  {field.value.open ? (
                    <span className={s.times}>
                      <input
                        className={`${inputClass} ${s.time}`}
                        type="time"
                        value={field.value.from}
                        aria-label={`${t(`profile.days.${d}`)} ${t('profile.from')}`}
                        onChange={(event) =>
                          field.onChange({ ...field.value, from: event.target.value })
                        }
                      />
                      <span aria-hidden="true">–</span>
                      <input
                        className={`${inputClass} ${s.time}`}
                        type="time"
                        value={field.value.to}
                        aria-label={`${t(`profile.days.${d}`)} ${t('profile.to')}`}
                        onChange={(event) =>
                          field.onChange({ ...field.value, to: event.target.value })
                        }
                      />
                    </span>
                  ) : (
                    <span className={s.closed}>{t('profile.closed')}</span>
                  )}
                </div>
              )}
            />
          ))}
        </Card>

        <Card>
          <Field label={t('profile.pickupInstructions')}>
            {(id) => <TextArea id={id} maxLength={1000} {...register('pickup_instructions')} />}
          </Field>
        </Card>
        <Card>
          <Field label={t('profile.returnPolicy')}>
            {(id) => <TextArea id={id} maxLength={1000} {...register('return_policy')} />}
          </Field>
        </Card>

        <BottomBar>
          <Button
            type="submit"
            variant="primary"
            block
            busy={save.isPending}
            disabled={!formState.isDirty && !save.isError}
          >
            {t('common.save')}
          </Button>
        </BottomBar>
      </form>
    </Page>
  );
}

// --- Payment accounts ------------------------------------------------------------------

function AccountsCard({
  accounts,
}: {
  accounts: UseFieldArrayReturn<FormValues, 'payment_accounts'>;
}) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState<number | 'new' | null>(null);
  const current = typeof editing === 'number' ? accounts.fields[editing] : undefined;
  return (
    <Card label={t('profile.paymentAccounts')}>
      {accounts.fields.map((field, index) => (
        <Row
          key={field.id}
          icon="card"
          title={field.name}
          subtitle={field.number}
          end={<Icon name="chevronRight" />}
          onClick={() => setEditing(index)}
        />
      ))}
      {accounts.fields.length < 10 && (
        <Button icon="plus" block onClick={() => setEditing('new')}>
          {t('profile.addAccount')}
        </Button>
      )}
      <PairSheet
        key={`account-${String(editing)}`}
        open={editing !== null}
        title={editing === 'new' ? t('profile.addAccount') : t('profile.paymentAccounts')}
        first={{
          label: t('profile.accountName'),
          placeholder: t('profile.accountNamePlaceholder'),
          value: current?.name ?? '',
        }}
        second={{
          label: t('profile.accountNumber'),
          value: current?.number ?? '',
          inputMode: 'text',
        }}
        third={{ label: t('profile.accountHolder'), value: current?.holder ?? '' }}
        onClose={() => setEditing(null)}
        onRemove={
          typeof editing === 'number'
            ? () => {
                accounts.remove(editing);
                setEditing(null);
              }
            : undefined
        }
        onSave={(name, number, holder) => {
          const value = { name, number, holder };
          if (editing === 'new') accounts.append(value);
          else if (typeof editing === 'number') accounts.update(editing, value);
          setEditing(null);
        }}
      />
    </Card>
  );
}

// --- Delivery areas ----------------------------------------------------------------------

function AreasCard({ areas }: { areas: UseFieldArrayReturn<FormValues, 'delivery_areas'> }) {
  const { t } = useTranslation();
  const language = useLanguage();
  const [editing, setEditing] = useState<number | 'new' | null>(null);
  const current = typeof editing === 'number' ? areas.fields[editing] : undefined;
  return (
    <Card label={t('profile.deliveryAreas')}>
      {areas.fields.map((field, index) => (
        <Row
          key={field.id}
          title={field.area}
          end={
            <span className={s.fee}>
              {money(field.fee, language)} <Icon name="chevronRight" />
            </span>
          }
          onClick={() => setEditing(index)}
        />
      ))}
      {areas.fields.length < 30 && (
        <Button icon="plus" block onClick={() => setEditing('new')}>
          {t('profile.addArea')}
        </Button>
      )}
      <PairSheet
        key={`area-${String(editing)}`}
        open={editing !== null}
        title={editing === 'new' ? t('profile.addArea') : t('profile.deliveryAreas')}
        first={{
          label: t('profile.area'),
          placeholder: t('profile.areaPlaceholder'),
          value: current?.area ?? '',
        }}
        second={{
          label: t('profile.fee'),
          value: current ? String(current.fee) : '',
          inputMode: 'decimal',
        }}
        onClose={() => setEditing(null)}
        onRemove={
          typeof editing === 'number'
            ? () => {
                areas.remove(editing);
                setEditing(null);
              }
            : undefined
        }
        onSave={(name, fee) => {
          const value = { area: name, fee: Number(fee) };
          if (editing === 'new') areas.append(value);
          else if (typeof editing === 'number') areas.update(editing, value);
          setEditing(null);
        }}
      />
    </Card>
  );
}

interface Input {
  label: string;
  value: string;
  placeholder?: string;
  inputMode?: 'text' | 'decimal';
}

/** A small sheet with two (or three) fields: an account or a delivery area. */
function PairSheet({
  open,
  title,
  first,
  second,
  third,
  onClose,
  onSave,
  onRemove,
}: {
  open: boolean;
  title: string;
  first: Input;
  second: Input;
  third?: Input;
  onClose: () => void;
  onSave: (first: string, second: string, third: string) => void;
  onRemove?: () => void;
}) {
  const { t } = useTranslation();
  const [a, setA] = useState(first.value);
  const [b, setB] = useState(second.value);
  const [c, setC] = useState(third?.value ?? '');
  const [error, setError] = useState('');

  const submit = () => {
    const decimal = second.inputMode === 'decimal';
    if (!a.trim() || !b.trim() || (decimal && !/^\d+(\.\d{1,2})?$/.test(b.trim()))) {
      setError(t('profile.required'));
      return;
    }
    onSave(a.trim(), b.trim(), c.trim());
  };

  return (
    <Sheet
      open={open}
      onClose={onClose}
      label={title}
      footer={
        <div className={s.sheetButtons}>
          {onRemove && (
            <Button variant="dangerOutline" icon="trash" onClick={onRemove}>
              {t('common.remove')}
            </Button>
          )}
          <Button variant="primary" block onClick={submit}>
            {t('common.done')}
          </Button>
        </div>
      }
    >
      <Field label={first.label}>
        {(id) => (
          <TextInput
            id={id}
            value={a}
            placeholder={first.placeholder}
            maxLength={60}
            onChange={(e) => setA(e.target.value)}
          />
        )}
      </Field>
      <Field label={second.label} error={error}>
        {(id) => (
          <TextInput
            id={id}
            value={b}
            inputMode={second.inputMode}
            maxLength={60}
            onChange={(e) => setB(e.target.value)}
          />
        )}
      </Field>
      {third && (
        <Field label={third.label}>
          {(id) => (
            <TextInput id={id} value={c} maxLength={60} onChange={(e) => setC(e.target.value)} />
          )}
        </Field>
      )}
    </Sheet>
  );
}
