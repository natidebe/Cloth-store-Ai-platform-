/** The backend's responses (docs/mini-app-api.md). Money comes as strings or numbers. */

export type Role = 'owner' | 'staff';
export type StoreStatus = 'pending' | 'active' | 'suspended';
export type Plan = 'free' | 'basic' | 'pro';
export type Period = 'today' | 'week' | 'month' | '7d' | '30d';
export type Money = string | number;

/** Phase 13: the kind of shop; it names a product's two options. */
export type ShopType = 'clothing' | 'electronics' | 'cosmetics' | 'general';

/** One option's name, e.g. Storage / ማከማቻ (plural for lists, emoji for posts). */
export interface OptionLabel {
  en: string;
  am: string;
  plural: string;
  icon: string;
}

/** A type with its own words (for "What kind of shop?" and Settings). */
export interface ShopTypeInfo {
  type: ShopType;
  names: { en: string; am: string };
  option1: OptionLabel;
  option2: OptionLabel;
  categories: string[];
  condition_and_warranty: boolean;
}

/** The owner's renames: empty = the type's word. */
export type OptionRenames = Partial<
  Record<'option1' | 'option2', { en?: string; am?: string }>
> | null;

export type Condition = 'new' | 'used';

export interface Me {
  user: { id: number; name: string; username: string | null; language_code: string | null };
  role: Role;
  store: {
    id: string;
    name: string;
    status: StoreStatus;
    plan: Plan | null;
    bot_username: string | null;
    staff_group_linked: boolean;
    channel_linked: boolean;
    /** Counter sales (Phase 12): staff may sell this much below the listed price at most. */
    staff_discount_percent: Money;
    /** "Cash" plus the store's payment accounts (anything else: "Other" + a note). */
    payment_methods: string[];
    /** Phase 13: the kind of shop and its words for the two options (with renames). */
    shop_type: ShopType;
    option1: OptionLabel;
    option2: OptionLabel;
    categories: string[];
    condition_and_warranty: boolean;
  };
}

export interface Variant {
  id: string;
  color: string | null;
  size: string | null;
  stock: number;
  price_override: Money | null;
  price: Money | null;
}

export interface Product {
  id: string;
  code: string | null;
  name: string;
  brand: string | null;
  category: string | null;
  base_price: Money | null;
  photo_url: string | null;
  description: string | null;
  search_keywords: string | null;
  /** Electronics (Phase 13): new or used, and months of warranty. */
  condition: Condition | null;
  warranty_months: number | null;
  total_stock: number;
  variant_count: number;
  on_sale: boolean;
  low_stock: boolean;
  price_min: Money | null;
  price_max: Money | null;
  variants: Variant[];
}

export interface ProductList {
  products: Product[];
  categories: string[];
}

export interface ProductFields {
  name?: string;
  brand?: string | null;
  category?: string | null;
  base_price?: Money | null;
  description?: string | null;
  search_keywords?: string | null;
  photo_url?: string | null;
  condition?: Condition | null;
  warranty_months?: number | null;
}

export interface GridRow {
  id?: string;
  color: string | null;
  size: string | null;
  stock: number;
  price?: Money | null;
}

export interface GridResult {
  product: Product;
  added: number;
  updated: number;
  removed: number;
  note: string;
}

export interface Analytics {
  period: Period;
  from: string;
  to: string;
  revenue: Money;
  payments: number;
  average_order: Money;
  orders_placed: number;
  orders_paid: number;
  paid_rate: number;
  unpaid_orders: number;
  delivery_orders: number;
  pickup_orders: number;
  new_customers: number;
  per_day: { day: string; placed: number; paid: number; revenue: Money }[];
  top_products: {
    product_id: string;
    name: string;
    code: string | null;
    quantity: number;
    revenue: Money;
  }[];
  low_stock: {
    variant_id: string;
    product_id: string;
    product_name: string;
    code: string | null;
    color: string | null;
    size: string | null;
    stock: number;
  }[];
  ai_calls_today: number;
  ai_daily_limit: number | null;
  // Phase 12: Telegram vs in shop, discounts, sellers
  telegram_orders: number;
  in_shop_sales: number;
  in_shop_revenue: Money;
  discount_total: Money;
  discounted_items: number;
  sellers: {
    telegram_id: number | null;
    name: string | null;
    sales: number;
    revenue: Money;
    discount: Money;
  }[];
}

export interface OrderItem {
  name: string | null;
  code: string | null;
  color: string | null;
  size: string | null;
  quantity: number;
  price: Money;
  /** The listed price when it was sold (counter sales); null = the price paid. */
  list_price: Money | null;
}

export type OrderChannel = 'telegram' | 'in_shop';

export interface Order {
  id: string;
  number: string;
  status: 'pending' | 'confirmed' | 'out_for_delivery' | 'delivered' | 'cancelled';
  payment_status: 'unpaid' | 'paid' | 'refunded';
  total: Money;
  currency: string;
  fulfillment: 'delivery' | 'pickup' | null;
  customer: { name: string | null; phone: string | null };
  delivery_address: string | null;
  created_at: string;
  items: OrderItem[];
  channel: OrderChannel;
  payment_method: string | null;
  payment_note: string | null;
  sold_by: string | null;
  note: string | null;
}

export interface OrderPage {
  orders: Order[];
  more: boolean;
}

export type OrderFilter = 'all' | OrderChannel;

/** Before selling (Phase 12): stock, and online orders holding it. */
export interface Availability {
  variant_id: string;
  stock: number;
  held: number;
  available: number;
  listed_price: Money | null;
  holds: { order_number: string; quantity: number; minutes_left: number }[];
}

export interface CounterSaleInput {
  items: { variant_id: string; quantity: number; price: string }[];
  payment_method: string;
  payment_note?: string | null;
  customer_name?: string | null;
  customer_phone?: string | null;
  note?: string | null;
  allow_held: boolean;
  request_id: string;
}

export interface CounterSaleResult {
  order_id: string;
  number: string;
  total: Money;
  list_total: Money;
  discount: Money;
  already_saved: boolean;
  held_orders: string[];
}

export interface PaymentAccount {
  name: string;
  number: string;
  holder?: string | null;
}

export interface DeliveryArea {
  area: string;
  fee: number;
}

export interface DayHours {
  open: boolean;
  from?: string | null;
  to?: string | null;
}

export const WEEK_DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const;
export type WeekDay = (typeof WEEK_DAYS)[number];
export type OpeningWeek = Record<WeekDay, DayHours>;

export interface StoreSettings {
  payment_accounts: PaymentAccount[];
  delivery_areas: DeliveryArea[];
  opening_week: OpeningWeek | null;
  location: string | null;
  pickup_instructions: string | null;
  return_policy: string | null;
  staff_discount_percent: Money;
  /** Phase 13: the kind of shop, the owner's renames, every type's own words. */
  shop_type: ShopType;
  option_labels: OptionRenames;
  shop_types: ShopTypeInfo[];
  // The texts customers get (written by the backend from the lists).
  payment_instructions: string | null;
  delivery_info: string | null;
  opening_hours: string | null;
}

export interface Connections {
  staff_group: { id: number; title: string | null; bot_can_see: boolean } | null;
  channel: { id: number; title: string | null; bot_can_see: boolean } | null;
}

export interface LinkCode {
  code: string;
  command: string;
  minutes: number;
  expires_at: string;
}

export interface BotChange {
  bot_username: string | null;
  bot_connected: boolean;
  note: string;
}

export interface StoreCard {
  id: string;
  name: string;
  status: StoreStatus;
  plan: Plan | null;
  bot_username: string | null;
  staff_group_linked: boolean;
  channel_linked: boolean;
  dashboard_url: string;
}

export interface PlatformMe {
  user: { id: number; name: string; username: string | null; language_code: string | null };
  is_platform_admin: boolean;
  stores: StoreCard[];
  support_url: string | null;
  /** Phase 13: for "What kind of shop?". */
  shop_types: ShopTypeInfo[];
}

export interface AdminStore {
  id: string;
  name: string;
  status: StoreStatus;
  plan: Plan | string;
  telegram_bot_username: string | null;
  created_at: string | null;
  orders: number;
}
