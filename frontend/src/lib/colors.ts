/**
 * A swatch for a color name in the stock grid (design: a dot before
 * "Black", "White", "Olive"). Unknown names get a neutral grey.
 */
const SWATCHES: Record<string, string> = {
  black: '#1c1c1e',
  white: '#f4f4f0',
  grey: '#8e8e93',
  gray: '#8e8e93',
  red: '#c8352b',
  blue: '#2a5fb0',
  navy: '#1f2f55',
  green: '#2f7d45',
  olive: '#5f6b3f',
  yellow: '#e2b93b',
  orange: '#e07b2a',
  brown: '#7a5235',
  beige: '#d9c7a7',
  cream: '#efe6d0',
  pink: '#e48bb0',
  purple: '#6d4aa0',
  // Amharic
  ጥቁር: '#1c1c1e',
  ነጭ: '#f4f4f0',
  ቀይ: '#c8352b',
  ሰማያዊ: '#2a5fb0',
  አረንጓዴ: '#2f7d45',
  ቢጫ: '#e2b93b',
  ቡናማ: '#7a5235',
  ግራጫ: '#8e8e93',
  ሮዝ: '#e48bb0',
};

export function swatch(color: string | null): string {
  const name = (color ?? '').trim().toLowerCase();
  if (!name) return 'transparent';
  return SWATCHES[name] ?? SWATCHES[name.split(/\s+/).at(-1) ?? ''] ?? '#b8b8c0';
}
