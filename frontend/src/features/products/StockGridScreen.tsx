import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useBlocker, useParams } from 'react-router';

import { usePublish, useProduct, useSaveGrid, useTakeOffSale } from '@/api/queries';
import type { Product } from '@/api/types';
import { ErrorScreen } from '@/app/AccessScreens';
import { useBackButton, useErrorText, useLanguage, useStore, useShopWords } from '@/app/hooks';
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
  TextInput,
} from '@/components/ui';
import { swatch } from '@/lib/colors';
import { money, variantLabel } from '@/lib/format';
import { confirmAction, haptic } from '@/lib/telegram';
import { axis, cellKey, splitKey, useGridDraft } from '@/state/gridDraft';
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
  const words = useShopWords();
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

  const colors = axis(draft.colors);
  const sizes = axis(draft.sizes);
  // No colors and no sizes (a bag, a belt, jewelry): just one stock counter.
  const plain = colors.length === 1 && colors[0] === '' && sizes.length === 1 && sizes[0] === '';
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

  /** Add a color or size (both optional); then the first box is ready for its stock. */
  const onAdd = (name: string): boolean => {
    const added = adding === 'color' ? draft.addColor(name) : draft.addSize(name);
    if (!added) return false;
    setAdding(null);
    const { colors, sizes, selected } = useGridDraft.getState();
    if (!selected) draft.select(cellKey(axis(colors)[0] ?? '', axis(sizes)[0] ?? ''));
    return true;
  };

  const onTakeOffSale = async () => {
    if (!(await confirmAction(t('grid.takeOffSaleConfirm', words)))) return;
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

      {plain ? (
        <Card label={t('grid.inStock')}>
          <CellEditor cellKey={PLAIN} basePrice={basePrice} canRemove={false} />
        </Card>
      ) : (
        <>
          <Card>
            <div className={s.scroll}>
              <table className={s.grid}>
                <thead>
                  <tr>
                    <th aria-hidden="true" />
                    {sizes.map((size) => (
                      <th key={size} scope="col">
                        {size || t('grid.noSize', words)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {colors.map((color) => (
                    <tr key={color}>
                      <th scope="row" className={s.colorHead}>
                        {color && <span className={s.dot} style={{ background: swatch(color) }} />}
                        <span className={s.colorName}>{color || t('grid.noColor', words)}</span>
                      </th>
                      {sizes.map((size) => {
                        const key = cellKey(color, size);
                        const cell = draft.cells[key];
                        const label = variantLabel(color, size) || t('grid.inStock');
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
                                cell
                                  ? `${label}: ${cell.stock}`
                                  : `${t('grid.addSize', words)} ${label}`
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
                                  <span className={s.cellPrice}>
                                    {cell.price ? cell.price : '—'}
                                  </span>
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
          </Card>
          <Card>
            {draft.selected && draft.cells[draft.selected] ? (
              <>
                <p className={s.selectedTitle}>
                  {t('grid.selected', {
                    label: variantLabel(selColor, selSize) || t('grid.inStock'),
                  })}
                </p>
                <CellEditor cellKey={draft.selected} basePrice={basePrice} canRemove />
              </>
            ) : (
              <p className={s.emptyText}>{t('grid.tapCell')}</p>
            )}
          </Card>
        </>
      )}

      <Card label={t('grid.optionalTitle', words)}>
        <p className={s.emptyText}>{t('grid.optionalHint', words)}</p>
        <div className={s.addRow}>
          <Button icon="plus" block onClick={() => setAdding('color')}>
            {t('grid.addColor', words)}
          </Button>
          <Button icon="plus" block onClick={() => setAdding('size')}>
            {t('grid.addSize', words)}
          </Button>
        </div>
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

const PLAIN = cellKey('', '');

/** The stock and own price of one box (for a plain product: its only one). */
function CellEditor({
  cellKey: key,
  basePrice,
  canRemove,
}: {
  cellKey: string;
  basePrice: Product['base_price'];
  canRemove: boolean;
}) {
  const { t } = useTranslation();
  const language = useLanguage();
  const { isOwner } = useStore();
  const draft = useGridDraft();
  const cell = draft.cells[key];
  const [color, size] = splitKey(key);
  // A plain product's box exists once something is set in it.
  const ensure = () => {
    if (!useGridDraft.getState().cells[key]) draft.select(key);
  };
  return (
    <>
      <div className={s.stockLine}>
        <span>{t('grid.stock')}</span>
        <Stepper
          label={t('grid.stock')}
          value={cell?.stock ?? 0}
          onChange={(n) => {
            ensure();
            draft.setStock(key, n);
          }}
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
            value={cell?.price ?? ''}
            locked={!isOwner}
            placeholder={
              basePrice !== null ? money(basePrice, language) : t('product.pricePlaceholder')
            }
            onChange={(event) => {
              ensure();
              draft.setPrice(key, event.target.value.replace(/[^\d.]/g, ''));
            }}
          />
        )}
      </Field>
      {canRemove && (
        <Button variant="link" onClick={() => draft.removeCell(key)}>
          {t('grid.removeVariant', { label: variantLabel(color, size) || t('grid.inStock') })}
        </Button>
      )}
    </>
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
  const words = useShopWords();
  const [name, setName] = useState('');
  const [error, setError] = useState('');
  const submit = () => {
    if (!name.trim()) return;
    if (!onAdd(name)) setError(t('grid.exists'));
  };
  const label = kind === 'color' ? t('grid.addColor', words) : t('grid.addSize', words);
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
      <Field
        label={kind === 'color' ? t('grid.colorPrompt', words) : t('grid.sizePrompt', words)}
        error={error}
      >
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
