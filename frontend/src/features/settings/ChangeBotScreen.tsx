import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Navigate } from 'react-router';

import { useChangeBot } from '@/api/queries';
import { useBackButton, useErrorText, useStore } from '@/app/hooks';
import {
  BottomBar,
  Button,
  Card,
  Field,
  Notice,
  Page,
  PageHeader,
  TextInput,
} from '@/components/ui';
import { confirmAction, haptic } from '@/lib/telegram';
import { toast } from '@/state/toasts';

const TOKEN = /^\d{5,15}:[A-Za-z0-9_-]{30,50}$/;

/** Design: "Change bot". */
export function ChangeBotScreen() {
  const { t } = useTranslation();
  const errorText = useErrorText();
  const { storeId, role, isOwner, store } = useStore();
  const change = useChangeBot(storeId);
  const [token, setToken] = useState('');
  const [error, setError] = useState('');
  useBackButton(`/s/${storeId}/settings`);

  if (!isOwner) return <Navigate to={`/s/${storeId}/settings`} replace />;

  const submit = async () => {
    const value = token.trim();
    if (!TOKEN.test(value)) {
      setError(t('bot.tokenInvalid'));
      return;
    }
    if (!(await confirmAction(t('bot.confirm')))) return;
    change.mutate(value, {
      onSuccess: (result) => {
        haptic.success();
        setToken('');
        toast.info(result.note || t('bot.switched', { username: result.bot_username ?? '' }));
      },
      onError: (failure) => {
        haptic.error();
        setError(errorText(failure));
      },
    });
  };

  return (
    <Page>
      <PageHeader title={t('bot.title')} role={role} />
      <Notice tone="warn" icon="warning">
        {t('bot.warning')}
      </Notice>
      <Card>
        <Field label={t('bot.current')}>
          {(id) => (
            <TextInput
              id={id}
              value={store.bot_username ? `@${store.bot_username}` : '—'}
              locked
              readOnly
            />
          )}
        </Field>
        <Field label={t('bot.newToken')} hint={t('bot.hint')} error={error}>
          {(id) => (
            <TextInput
              id={id}
              value={token}
              placeholder="123456789:AAH…"
              autoComplete="off"
              spellCheck={false}
              style={{ fontFamily: 'var(--mono)' }}
              invalid={Boolean(error)}
              onChange={(event) => {
                setToken(event.target.value);
                setError('');
              }}
            />
          )}
        </Field>
      </Card>
      <BottomBar>
        <Button
          variant="primary"
          block
          busy={change.isPending}
          disabled={!token.trim()}
          onClick={() => void submit()}
        >
          {t('bot.switch')}
        </Button>
      </BottomBar>
    </Page>
  );
}
