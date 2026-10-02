import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Navigate } from 'react-router';

import { keys, useConnections, useLinkCode } from '@/api/queries';
import type { Connections } from '@/api/types';
import { useBackButton, useErrorText, useStore } from '@/app/hooks';
import { Icon } from '@/components/Icon';
import { Badge, BottomBar, Button, Card, Page, PageHeader, Steps } from '@/components/ui';
import { haptic } from '@/lib/telegram';
import { toast } from '@/state/toasts';

import s from './settings.module.css';

const signature = (c: Connections | undefined) =>
  `${c?.staff_group?.id ?? ''}|${c?.channel?.id ?? ''}`;

/** Design: "Connect staff group or channel". */
export function ConnectScreen() {
  const { t } = useTranslation();
  const errorText = useErrorText();
  const queryClient = useQueryClient();
  const { storeId, role, isOwner } = useStore();
  const linkCode = useLinkCode(storeId);
  const [waiting, setWaiting] = useState(true);
  const connections = useConnections(storeId, waiting && linkCode.isSuccess);
  const before = useRef<string | null>(null);
  const asked = useRef(false);
  useBackButton(`/s/${storeId}/settings`);

  // One code per visit (the ref also stops React's development double-run).
  useEffect(() => {
    if (!isOwner || asked.current) return;
    asked.current = true;
    linkCode.mutate(undefined, { onError: (error) => toast.error(errorText(error)) });
  }, [isOwner, linkCode, errorText]);

  // The bot saw the code: the group or channel changed.
  useEffect(() => {
    if (!connections.data) return;
    const now = signature(connections.data);
    if (before.current === null) before.current = now;
    else if (now !== before.current && waiting) {
      setWaiting(false);
      haptic.success();
      toast.info(t('connect.linked'));
      void queryClient.invalidateQueries({ queryKey: keys.me(storeId) });
    }
  }, [connections.data, waiting, queryClient, storeId, t]);

  if (!isOwner) return <Navigate to={`/s/${storeId}/settings`} replace />;

  const command = linkCode.data?.command ?? '/link …';
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command);
      haptic.success();
      toast.info(t('common.copied'));
    } catch {
      toast.error(command);
    }
  };

  const linked = [
    connections.data?.staff_group && {
      ...connections.data.staff_group,
      kind: t('connect.staffGroup'),
    },
    connections.data?.channel && { ...connections.data.channel, kind: t('connect.channel') },
  ].filter((c): c is NonNullable<typeof c> => Boolean(c));

  return (
    <Page>
      <PageHeader title={t('connect.title')} role={role} />
      <Card>
        <Steps items={[t('connect.step1'), t('connect.step2')]} />
        <div className={s.codeBox}>
          <span className={s.code}>{command}</span>
          <Button variant="soft" onClick={() => void copy()} disabled={!linkCode.data}>
            {t('common.copy')}
          </Button>
        </div>
        <Steps items={[t('connect.step3')]} />
        {linkCode.data && waiting && (
          <p className={s.waiting} role="status">
            <span className={s.waitingDot} aria-hidden="true" />
            {t('connect.waiting')}
          </p>
        )}
        {linkCode.data && (
          <p className={s.waiting}>{t('connect.expires', { minutes: linkCode.data.minutes })}</p>
        )}
      </Card>

      {linked.length > 0 && (
        <Card label={t('connect.connected')}>
          {linked.map((chat) => (
            <div key={chat.id} className={s.connected}>
              <span className={s.check}>
                <Icon name="check" size={16} />
              </span>
              <span className={s.connectedName}>{chat.title ?? t('connect.hidden')}</span>
              <Badge tone={chat.kind === t('connect.staffGroup') ? 'info' : 'neutral'}>
                {chat.kind}
              </Badge>
            </div>
          ))}
        </Card>
      )}

      <BottomBar>
        <Button variant="primary" block onClick={() => void copy()} disabled={!linkCode.data}>
          {t('connect.copyCode')}
        </Button>
      </BottomBar>
    </Page>
  );
}
