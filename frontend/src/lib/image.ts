/**
 * Shrink a phone photo before uploading: at most 1600 px on the long side,
 * JPEG. Phone cameras make 5–12 MB photos; the server takes up to 5 MB, and
 * a smaller photo also loads faster in the channel.
 */
const MAX_SIDE = 1600;
const QUALITY = 0.85;
export const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;
export const PHOTO_TYPES = ['image/jpeg', 'image/png', 'image/webp'];

export async function shrinkPhoto(file: File): Promise<Blob> {
  if (!('createImageBitmap' in window)) return file;
  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
    if (scale === 1 && file.size <= MAX_UPLOAD_BYTES && file.type === 'image/jpeg') {
      bitmap.close();
      return file;
    }
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext('2d')?.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close();
    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, 'image/jpeg', QUALITY),
    );
    return blob ?? file;
  } catch {
    return file; // an unusual format: let the server decide
  }
}
