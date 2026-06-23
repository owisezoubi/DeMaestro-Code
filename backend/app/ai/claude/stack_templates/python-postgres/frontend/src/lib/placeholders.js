/**
 * Stable placeholder image URLs for generated apps.
 * These services are free, public, require no API key, and reliably resolve.
 * Use these whenever real user-provided images aren't available so the UI
 * never looks broken.
 */
export const PLACEHOLDER = {
  profile:  "https://api.dicebear.com/9.x/initials/svg?seed=USER&backgroundType=gradientLinear&backgroundColor=ffd5dc,d1fae5,dbeafe&fontFamily=Inter",
  landscape: (seed = 1) => `https://picsum.photos/seed/landscape${seed}/1600/900`,
  portrait:  (seed = 1) => `https://picsum.photos/seed/portrait${seed}/900/1200`,
  square:    (seed = 1) => `https://picsum.photos/seed/square${seed}/800/800`,
  food:      (seed = 1) => `https://picsum.photos/seed/food${seed}/800/600`,
  nature:    (seed = 1) => `https://picsum.photos/seed/nature${seed}/800/600`,
  abstract:  (seed = 1) => `https://picsum.photos/seed/abstract${seed}/800/600`,
}

/** Build a DiceBear initials avatar URL for a user. */
export function avatarUrl(name) {
  const seed = encodeURIComponent((name || "?").trim() || "?")
  return `https://api.dicebear.com/9.x/initials/svg?seed=${seed}&backgroundType=gradientLinear&backgroundColor=ffd5dc,d1fae5,dbeafe`
}
