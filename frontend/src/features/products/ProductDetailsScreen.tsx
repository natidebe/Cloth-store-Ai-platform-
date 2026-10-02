import { zodResolver } from '@hookform/resolvers/zod';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Controller, useForm, useWatch } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router';
import { z } from 'zod';

import {
  useCreateProduct,
  useProduct,
  useProducts,
  useUpdateProduct,
  useUploadPhoto,
} from '@/api/queries';
import type { Product, ProductFields } from '@/api/types';
import { ErrorScreen } from '@/app/AccessScreens';
import { useBackButton, useErrorText, useStore } from '@/app/hooks';
import { inputClass, selectClass } from '@/components/classes';
import { Icon } from '@/components/Icon';
import {
  BottomBar,
  Button,
  Card,
  Field,
  Page,
  PageHeader,
  Photo,
  SkeletonList,
  TextArea,
  TextInput,
} from '@/components/ui';
import { capitalize, joinKeywords, splitKeywords } from '@/lib/format';
import { MAX_UPLOAD_BYTES, PHOTO_TYPES, shrinkPhoto } from '@/lib/image';
import { haptic } from '@/lib/telegram';
import { toast } from '@/state/toasts';

import s from './details.module.css';

const NEW_CATEGORY = '__new__';
const DESCRIPTION_MAX = 700;

function schema(t: (key: 'product.nameRequired' | 'product.priceInvalid') => string) {
  return z.object({
    name: z.string().trim().min(1, t('product.nameRequired')).max(80),
    brand: z.string().trim().max(60),
    category: z.string().trim().max(40),
    price: z
      .string()
      .trim()
      .refine(
        (v) => v === '' || (/^\d+(\.\d{1,2})?$/.test(v) && Number(v) <= 10_000_000),
        t('product.priceInvalid'),
      ),
    description: z.string().max(DESCRIPTION_MAX),
    keywords: z.array(z.string()),
    photoUrl: z.string().nullable(),
  });
}

type FormValues = z.infer<ReturnType<typeof schema>>;

function toForm(product?: Product): FormValues {
  return {
    name: product?.name ?? '',
    brand: product?.brand ?? '',
    category: product?.category ?? '',
    price:
      product?.base_price === null || product?.base_price === undefined
        ? ''
        : String(product.base_price),
    description: product?.description ?? '',
    keywords: splitKeywords(product?.search_keywords),
    photoUrl: product?.photo_url ?? null,
  };
}

/** Design: "Add edit details" (new and edit), "Staff price locked". */
export function ProductDetailsScreen() {
  const { productId } = useParams<{ productId: string }>();
  const { storeId } = useStore();
  const product = useProduct(storeId, productId);
  useBackButton(`/s/${storeId}/products`);

  if (productId && product.isPending) {
    return (
      <Page>
        <SkeletonList rows={3} />
      </Page>
    );
  }
  if (productId && product.isError)
    return <ErrorScreen error={product.error} onRetry={() => void product.refetch()} />;
  return <DetailsForm key={productId ?? 'new'} product={product.data} />;
}

function DetailsForm({ product }: { product?: Product }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const errorText = useErrorText();
  const { storeId, role, isOwner } = useStore();
  const categories = useProducts(storeId).data?.categories;
  const create = useCreateProduct(storeId);
  const update = useUpdateProduct(storeId, product?.id ?? '');
  const upload = useUploadPhoto(storeId);
  const [newCategory, setNewCategory] = useState(false);

  const resolver = useMemo(() => zodResolver(schema(t)), [t]);
  const { register, control, handleSubmit, formState, setValue, reset } = useForm<FormValues>({
    resolver,
    defaultValues: toForm(product),
  });
  // A refresh from the server must not wipe what the user is typing.
  useEffect(() => reset(toForm(product), { keepDirtyValues: true }), [product, reset]);

  const [description, photoUrl, category] = useWatch({
    control,
    name: ['description', 'photoUrl', 'category'],
  });
  const options = useMemo(() => {
    const list = new Set(categories ?? []);
    if (category) list.add(category);
    return [...list].sort();
  }, [categories, category]);

  const onPhoto = async (file: File | undefined) => {
    if (!file) return;
    if (!PHOTO_TYPES.includes(file.type) && !file.type.startsWith('image/')) {
      toast.error(t('product.photoType'));
      return;
    }
    const photo = await shrinkPhoto(file);
    if (photo.size > MAX_UPLOAD_BYTES) {
      toast.error(t('product.photoTooLarge'));
      return;
    }
    upload.mutate(
      { file: photo, name: 'photo.jpg' },
      {
        onSuccess: ({ photo_url }) => setValue('photoUrl', photo_url, { shouldDirty: true }),
        onError: (error) => toast.error(errorText(error)),
      },
    );
  };

  const onSubmit = handleSubmit((values) => {
    const fields: ProductFields = {
      name: values.name,
      brand: values.brand || null,
      category: values.category ? values.category.toLowerCase() : null,
      description: values.description.trim() || null,
      search_keywords: joinKeywords(values.keywords),
      photo_url: values.photoUrl,
    };
    if (isOwner) fields.base_price = values.price === '' ? null : values.price;

    const done = (saved: Product, isNew: boolean) => {
      haptic.success();
      toast.info(t('common.saved'));
      // A new product goes on to its colors and sizes.
      navigate(isNew ? `/s/${storeId}/products/${saved.id}/stock` : `/s/${storeId}/products`, {
        replace: isNew,
      });
    };
    const failed = (error: unknown) => {
      haptic.error();
      toast.error(errorText(error));
    };
    if (product)
      update.mutate(fields, { onSuccess: (saved) => done(saved, false), onError: failed });
    else create.mutate({ fields }, { onSuccess: (saved) => done(saved, true), onError: failed });
  });

  return (
    <Page>
      <PageHeader title={product ? t('product.editTitle') : t('product.newTitle')} role={role} />
      <form onSubmit={onSubmit} noValidate>
        <Card>
          <div className={s.photoRow}>
            <Photo url={photoUrl} size={104} alt={product?.name ?? ''} />
            <div className={s.photoButtons}>
              <PhotoButton
                icon="camera"
                label={t('product.takePhoto')}
                capture
                onFile={onPhoto}
                busy={upload.isPending}
              />
              <PhotoButton
                icon="image"
                label={t('product.chooseGallery')}
                onFile={onPhoto}
                busy={upload.isPending}
              />
              {upload.isPending && <span className={s.uploading}>{t('product.uploading')}</span>}
            </div>
          </div>

          <Field label={t('product.name')} error={formState.errors.name?.message}>
            {(id) => (
              <TextInput
                id={id}
                placeholder={t('product.namePlaceholder')}
                invalid={Boolean(formState.errors.name)}
                {...register('name')}
              />
            )}
          </Field>

          <Field label={t('product.brand')}>
            {(id) => <TextInput id={id} placeholder={t('product.brand')} {...register('brand')} />}
          </Field>

          <Field label={t('product.category')}>
            {(id) =>
              newCategory ? (
                <TextInput
                  id={id}
                  autoFocus
                  placeholder={t('product.newCategoryPrompt')}
                  {...register('category')}
                />
              ) : (
                <div className={s.selectWrap}>
                  <select
                    id={id}
                    className={selectClass}
                    value={category}
                    onChange={(event) => {
                      if (event.target.value === NEW_CATEGORY) {
                        setNewCategory(true);
                        setValue('category', '', { shouldDirty: true });
                      } else {
                        setValue('category', event.target.value, { shouldDirty: true });
                      }
                    }}
                  >
                    <option value="">{t('product.chooseCategory')}</option>
                    {options.map((c) => (
                      <option key={c} value={c}>
                        {capitalize(c)}
                      </option>
                    ))}
                    <option value={NEW_CATEGORY}>{t('product.newCategory')}</option>
                  </select>
                  <Icon name="chevronDown" size={18} className={s.selectIcon} />
                </div>
              )
            }
          </Field>

          <Field
            label={t('product.price')}
            error={formState.errors.price?.message}
            hint={isOwner ? undefined : t('product.priceLocked')}
          >
            {(id) => (
              <TextInput
                id={id}
                inputMode="decimal"
                placeholder={t('product.pricePlaceholder')}
                locked={!isOwner}
                invalid={Boolean(formState.errors.price)}
                {...register('price')}
              />
            )}
          </Field>
        </Card>

        <Card>
          <Field label={t('product.description')}>
            {(id) => (
              <TextArea
                id={id}
                maxLength={DESCRIPTION_MAX}
                counter={{ value: description.length, max: DESCRIPTION_MAX }}
                {...register('description')}
              />
            )}
          </Field>
          <Controller
            control={control}
            name="keywords"
            render={({ field }) => <Keywords value={field.value} onChange={field.onChange} />}
          />
        </Card>

        <BottomBar>
          <Button
            type="submit"
            variant="primary"
            block
            busy={create.isPending || update.isPending}
            disabled={upload.isPending}
          >
            {t('common.save')}
          </Button>
        </BottomBar>
      </form>
    </Page>
  );
}

function PhotoButton({
  icon,
  label,
  capture,
  onFile,
  busy,
}: {
  icon: 'camera' | 'image';
  label: string;
  capture?: boolean;
  onFile: (file: File | undefined) => void;
  busy: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <Button icon={icon} block disabled={busy} onClick={() => input.current?.click()}>
        {label}
      </Button>
      <input
        ref={input}
        type="file"
        accept="image/*"
        capture={capture ? 'environment' : undefined}
        hidden
        onChange={(event) => {
          onFile(event.target.files?.[0]);
          event.target.value = '';
        }}
      />
    </>
  );
}

function Keywords({
  value,
  onChange,
}: {
  value: string[];
  onChange: (keywords: string[]) => void;
}) {
  const { t } = useTranslation();
  const [adding, setAdding] = useState(false);
  const [text, setText] = useState('');
  const add = () => {
    const words = text
      .split(',')
      .map((w) => w.trim())
      .filter(Boolean);
    const next = [...value];
    for (const word of words)
      if (!next.some((k) => k.toLowerCase() === word.toLowerCase())) next.push(word);
    onChange(next.slice(0, 20));
    setText('');
    setAdding(false);
  };
  return (
    <div>
      <span className={s.keywordsLabel}>{t('product.keywords')}</span>
      <div className={s.keywords}>
        {value.map((keyword) => (
          <button
            key={keyword}
            type="button"
            className={s.keyword}
            onClick={() => onChange(value.filter((k) => k !== keyword))}
            aria-label={`${t('common.remove')} ${keyword}`}
          >
            {keyword} <span aria-hidden="true">×</span>
          </button>
        ))}
        {adding ? (
          <input
            className={`${inputClass} ${s.keywordInput}`}
            autoFocus
            value={text}
            placeholder={t('product.keywordPrompt')}
            onChange={(event) => setText(event.target.value)}
            onBlur={add}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                add();
              }
            }}
          />
        ) : (
          <button type="button" className={s.addKeyword} onClick={() => setAdding(true)}>
            <Icon name="plus" size={16} /> {t('product.addKeyword')}
          </button>
        )}
      </div>
    </div>
  );
}
