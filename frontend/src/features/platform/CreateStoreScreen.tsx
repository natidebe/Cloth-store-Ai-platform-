import { zodResolver } from '@hookform/resolvers/zod';
import { useMemo, useState } from 'react';
import { useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';
import { z } from 'zod';

import { useCreateStore } from '@/api/platformQueries';
import type { ShopType } from '@/api/types';
import { useErrorText, useLanguage } from '@/app/hooks';
import { ShopTypeChoices } from '@/components/ShopTypeChoices';
import {
  BottomBar,
  Button,
  Card,
  Field,
  Page,
  PageHeader,
  Steps,
  TextInput,
} from '@/components/ui';
import { haptic } from '@/lib/telegram';
import { toast } from '@/state/toasts';

import { useBackToPlatform, usePlatform } from './hooks';
import s from './platform.module.css';

const TOKEN = /^\d{5,15}:[A-Za-z0-9_-]{30,50}$/;

/**
 * Design: "Create your store". Phase 13: first "What kind of shop do you
 * have?" (it names the products' options everywhere), then the name and bot.
 */
export function CreateStoreScreen() {
  const { t } = useTranslation();
  const language = useLanguage();
  const { shop_types: types } = usePlatform();
  const [kind, setKind] = useState<ShopType | null>(null);
  const [kindChosen, setKindChosen] = useState(false);
  useBackToPlatform();

  if (!kindChosen || !kind) {
    return (
      <Page>
        <PageHeader title={t('platform.createTitle')} subtitle={t('platform.createSubtitle')} />
        <Card>
          <h2 className={s.kindTitle}>{t('platform.kindTitle')}</h2>
          <p className={s.kindHint}>{t('platform.kindHint')}</p>
          <ShopTypeChoices
            types={types}
            value={kind}
            onChange={setKind}
            language={language}
            label={t('platform.kindTitle')}
          />
        </Card>
        <BottomBar>
          <Button variant="primary" block disabled={!kind} onClick={() => setKindChosen(true)}>
            {t('platform.kindNext')}
          </Button>
        </BottomBar>
      </Page>
    );
  }
  const chosen = types.find((type) => type.type === kind);
  return (
    <StoreForm
      kind={kind}
      kindName={chosen ? chosen.names[language] : kind}
      onChangeKind={() => setKindChosen(false)}
    />
  );
}

function StoreForm({
  kind,
  kindName,
  onChangeKind,
}: {
  kind: ShopType;
  kindName: string;
  onChangeKind: () => void;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const errorText = useErrorText();
  const create = useCreateStore();

  const schema = useMemo(
    () =>
      z.object({
        name: z
          .string()
          .trim()
          .min(2, t('platform.nameInvalid'))
          .max(80, t('platform.nameInvalid')),
        token: z.string().trim().regex(TOKEN, t('bot.tokenInvalid')),
      }),
    [t],
  );
  const { register, handleSubmit, setValue, setError, formState } = useForm<z.infer<typeof schema>>(
    {
      resolver: zodResolver(schema),
      defaultValues: { name: '', token: '' },
    },
  );

  const paste = async () => {
    try {
      const text = (await navigator.clipboard.readText()).trim();
      setValue('token', text, { shouldValidate: true, shouldDirty: true });
    } catch {
      // Clipboard not allowed: the user pastes into the field by hand.
    }
  };

  const onSubmit = handleSubmit(({ name, token }) =>
    create.mutate(
      { name, token, shopType: kind },
      {
        onSuccess: () => {
          haptic.success();
          navigate('/platform', { replace: true });
        },
        onError: (error) => {
          haptic.error();
          const message = errorText(error);
          if (/token|bot/i.test(message)) setError('token', { message });
          else toast.error(message);
        },
      },
    ),
  );

  return (
    <Page>
      <PageHeader title={t('platform.createTitle')} subtitle={t('platform.createSubtitle')} />
      <form onSubmit={onSubmit} noValidate>
        <Card>
          <div className={s.kindChosen}>
            <span>
              <span className={s.kindChosenLabel}>{t('platform.kindTitle')}</span>
              <strong>{kindName}</strong>
            </span>
            <Button variant="link" onClick={onChangeKind}>
              {t('platform.kindChange')}
            </Button>
          </div>
          <Field label={t('platform.storeName')} error={formState.errors.name?.message}>
            {(id) => (
              <TextInput
                id={id}
                placeholder={t('platform.storeName')}
                maxLength={80}
                invalid={Boolean(formState.errors.name)}
                {...register('name')}
              />
            )}
          </Field>
          <Field
            label={t('platform.botToken')}
            error={formState.errors.token?.message}
            hint={t('platform.tokenPrivate')}
          >
            {(id) => (
              <>
                <TextInput
                  id={id}
                  className={s.tokenInput}
                  placeholder="123456789:AAH…"
                  autoComplete="off"
                  spellCheck={false}
                  invalid={Boolean(formState.errors.token)}
                  {...register('token')}
                />
                <div style={{ marginTop: 10 }}>
                  <Button variant="soft" onClick={() => void paste()}>
                    {t('platform.paste')}
                  </Button>
                </div>
              </>
            )}
          </Field>
        </Card>
        <Card label={t('platform.howTo')}>
          <Steps
            items={[
              t('platform.howTo1'),
              t('platform.howTo2'),
              t('platform.howTo3'),
              t('platform.howTo4'),
              t('platform.howTo5'),
            ]}
          />
        </Card>
        <BottomBar>
          <Button type="submit" variant="primary" block busy={create.isPending}>
            {t('platform.create')}
          </Button>
        </BottomBar>
      </form>
    </Page>
  );
}
