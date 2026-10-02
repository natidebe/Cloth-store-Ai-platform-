import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useBlocker, useParams } from 'react-router';

import { usePublish, useProduct, useSaveGrid, useTakeOffSale } from '@/api/queries';
import type { Product } from '@/api/types';
import { ErrorScreen } from '@/app/AccessScreens';
import { useBackButton, useErrorText, useLanguage, useStore } from '@/app/hooks';
import {
  BottomBar,
  Button,
  Card,
  Field,
  Page,
  PageHeader,
  Sheet,
  SkeletonList,
  Stepper,
  Steps,
  TextInput,
} from '@/components/ui';
import { swatch } from '@/lib/colors';
import { money, variantLabel } from '@/lib/format';
import { confirmAction, haptic } from '@/lib/telegram';
import { cellKey, splitKey, useGridDraft } from '@/state/gridDraft';
import { toast } from '@/state/toasts';

import s from './grid.module.css';

/** Design: "Add edit stock grid and actions" / "Stock grid, dark". */
export function StockGridScreen() {
  const { productId } = useParams<{ productId: string }>();
  const { storeId } = useStore();
  const product = useProduct(storeId, productId);
  useBackButton(`/s/${storeId}/products`);

  if (product.isPending) {
    return (
      <Page>
        <SkeletonList rows={4} />
      </Page>
    );
  }
  if (product.isError)
    return <ErrorScreen error={product.error} onRetry={() => void product.refetch()} />;
  return <Grid product={product.data} />;
}

function Grid({ product }: { product: Product }) {
  const { t } = useTranslation();
  const language = useLanguage();
  const errorText = useErrorText();
  const { storeId, role, isOwner } = useStore();
  const save = useSaveGrid(storeId, product.id);
  const publish = usePublish(storeId, product.id);
  const offSale = useTakeOffSale(storeId, product.id);
  const draft = useGridDraft();
  const [adding, setAdding] = useState<'color' | 'size' | null>(null);

  // Load the product into the draft once (and again after it's saved).
  useEffect(() => {
    if (draft.productId !== product.id || !draft.dirty) draft.load(product);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only when the product changes
  }, [product]);

  // Unsaved changes: ask before leaving.
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      draft.dirty && currentLocation.pathname !== nextLocation.pathname,
  );
  useEffect(() => {
    if (blocker.state !== 'blocked') return;
    void confirmAction(t('common.unsaved')).then((leave) =>
      leave ? blocker.proceed() : blocker.reset(),
    );
  }, [blocker, t]);

  const selected = draft.selected ? draft.cells[draft.selected] : undefined;
  const [selColor, selSize] = draft.selected ? splitKey(draft.selected) : ['', ''];
  const basePrice = product.base_price;

  const onSave = () =>
    save.mutate(
      { rows: draft.rows(), remove: draft.removedIds() },
      {
        onSuccess: (result) => {
          haptic.success();
          draft.load(result.product);
          toast.info(result.note || t('common.saved'));
        },
        onError: (error) => {
          haptic.error();
          toast.error(errorText(error));
        },
      },
    );

  /** Add a color or size, then guide to the next step: the other one, then the first box. */
  const onAdd = (name: string): boolean => {
    const added = adding === 'color' ? draft.addColor(name) : draft.addSize(name);
    if (!added) return false;
    const { colors, sizes, selected } = useGridDraft.getState();
    if (colors.length === 0) setAdding('color');
    else if (sizes.length === 0) setAdding('size');
    else {
      setAdding(null);
      const color = adding === 'color' ? name.trim() : colors[0];
      const size = adding === 'size' ? name.trim() : sizes[0];
      if (!selected && color !== undefined && size !== undefined)
        draft.select(cellKey(color, size));
    }
    return true;
  };

  const onTakeOffSale = async () => {
    if (!(await confirmAction(t('grid.takeOffSaleConfirm')))) return;
    offSale.mutate(undefined, {
      onSuccess: () => toast.info(t('grid.offSale')),
      onError: (error) => toast.error(errorText(error)),
    });
  };

  const onPublish = () =>
    publish.mutate(undefined, {
      onSuccess: () => {
        haptic.success();
        toast.info(t('grid.posted'));
      },
      onError: (error) => toast.error(errorText(error)),
    });

  return (
    <Page>
      <PageHeader title={t('grid.title')} subtitle={product.name} role={role} />

      <Card>
        {draft.colors.length > 0 && draft.sizes.length > 0 ? (
          <div className={s.scroll}>
            <table className={s.grid}>
              <thead>
                <tr>
                  <th aria-hidden="true" />
                  {draft.sizes.map((size) => (
                    <th key={size} scope="col">
                      {size || t('grid.noSize')}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {draft.colors.map((color) => (
                  <tr key={color}>
                    <th scope="row" className={s.colorHead}>
                      <span className={s.dot} style={{ background: swatch(color) }} />
                      <span className={s.colorName}>{color || t('grid.noColor')}</span>
                    </th>
                    {draft.sizes.map((size) => {
                      const key = cellKey(color, size);
                      const cell = draft.cells[key];
                      const label = variantLabel(color, size);
                      return (
                        <td key={size}>
                          <button
                            type="button"
                            className={[
                              s.cell,
                              !cell && s.empty,
                              cell?.stock === 0 && s.zero,
                              draft.selected === key && s.selected,
                            ]
                              .filter(Boolean)
                              .join(' ')}
                            aria-label={
                              cell ? `${label}: ${cell.stock}` : `${t('grid.addSize')} ${label}`
                            }
                            aria-pressed={draft.selected === key}
                            onClick={() => {
                              haptic.select();
                              draft.select(key);
                            }}
                          >
                            {cell ? (
                              <>
                                <span className={s.cellStock}>{cell.stock}</span>
                                <span className={s.cellPrice}>{cell.price ? cell.price : '—'}</span>
                              </>
                            ) : (
                              <span className={s.cellPlus}>+</span>
                            )}
                          </button>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <GridSteps colors={draft.colors} sizes={draft.sizes} />
        )}
        <div className={s.addRow}>
          <Button icon="plus" block onClick={() => setAdding('color')}>
            {t('grid.addColor')}
          </Button>
          <Button icon="plus" block onClick={() => setAdding('size')}>
            {t('grid.addSize')}
          </Button>
        </div>
      </Card>

      <Card>
        {selected && draft.selected ? (
          <>
            <p className={s.selectedTitle}>
              {t('grid.selected', { label: variantLabel(selColor, selSize) })}
            </p>
            <div className={s.stockLine}>
              <span>{t('grid.stock')}</span>
              <Stepper
                label={t('grid.stock')}
                value={selected.stock}
                onChange={(n) => draft.setStock(draft.selected as string, n)}
              />
            </div>
            <Field
              label={t('grid.ownPrice')}
              hint={isOwner ? t('grid.ownPriceHint') : t('product.priceLocked')}
            >
              {(id) => (
                <TextInput
                  id={id}
                  inputMode="decimal"
                  value={selected.price}
                  locked={!isOwner}
                  placeholder={
                    basePrice !== null ? money(basePrice, language) : t('product.pricePlaceholder')
                  }
                  onChange={(event) =>
                    draft.setPrice(
                      draft.selected as string,
                      event.target.value.replace(/[^\d.]/g, ''),
                    )
                  }
                />
              )}
            </Field>
            <Button variant="link" onClick={() => draft.removeCell(draft.selected as string)}>
              {t('grid.removeVariant', { label: variantLabel(selColor, selSize) })}
            </Button>
          </>
        ) : (
          <p className={s.emptyText}>{t('grid.tapCell')}</p>
        )}
      </Card>

      {isOwner && (
        <Card>
          <div className={s.actions}>
            <Button
              icon="send"
              block
              busy={publish.isPending}
              onClick={onPublish}
              disabled={draft.dirty}
            >
              {t('grid.postToChannel')}
            </Button>
            <Button
              variant="dangerOutline"
              block
              busy={offSale.isPending}
              onClick={() => void onTakeOffSale()}
            >
              {t('grid.takeOffSale')}
            </Button>
          </div>
        </Card>
      )}

      <BottomBar>
        <Button
          variant="primary"
          block
          busy={save.isPending}
          disabled={!draft.dirty}
          onClick={onSave}
        >
          {t('common.save')}
        </Button>
      </BottomBar>

      <AddSheet
        key={adding ?? 'closed'}
        kind={adding}
        onClose={() => setAdding(null)}
        onAdd={onAdd}
      />
    </Page>
  );
}

/** An empty grid: the three steps, with what's been added so far. */
function GridSteps({ colors, sizes }: { colors: string[]; sizes: string[] }) {
  const { t } = useTranslation();
  return (
    <div className={s.steps}>
      <Steps
        items={[
          <span key="c">
            {t('grid.step1')}
            {colors.length > 0 && <strong> {colors.join(', ')} ✓</strong>}
          </span>,
          <span key="s">
            {t('grid.step2')}
            {sizes.length > 0 && <strong> {sizes.join(', ')} ✓</strong>}
          </span>,
          t('grid.step3'),
        ]}
      />
    </div>
  );
}

function AddSheet({
  kind,
  onClose,
  onAdd,
}: {
  kind: 'color' | 'size' | null;
  onClose: () => void;
  onAdd: (name: string) => boolean;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [error, setError] = useState('');
  const submit = () => {
    if (!name.trim()) return;
    if (!onAdd(name)) setError(t('grid.exists'));
  };
  const label = kind === 'color' ? t('grid.addColor') : t('grid.addSize');
  return (
    <Sheet
      open={kind !== null}
      onClose={onClose}
      label={label}
      footer={
        <Button variant="primary" block onClick={submit} disabled={!name.trim()}>
          {label}
        </Button>
      }
    >
      <Field label={kind === 'color' ? t('grid.colorPrompt') : t('grid.sizePrompt')} error={error}>
        {(id) => (
          <TextInput
            id={id}
            autoFocus
            value={name}
            maxLength={kind === 'color' ? 30 : 20}
            onChange={(event) => {
              setName(event.target.value);
              setError('');
            }}
            onKeyDown={(event) => event.key === 'Enter' && submit()}
          />
        )}
      </Field>
    </Sheet>
  );
}
