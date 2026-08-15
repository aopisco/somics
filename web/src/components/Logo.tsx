/** Placeholder mark: four dots on a deep-blue field, per the design packet. */
export function Logo({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <rect width="24" height="24" rx="6" fill="#00114a" />
      <circle cx="8" cy="8" r="2.1" fill="#6ca6ff" />
      <circle cx="16" cy="8" r="1.3" fill="#9dc6ff" />
      <circle cx="8" cy="16" r="1.3" fill="#9dc6ff" />
      <circle cx="16" cy="16" r="3.1" fill="#1a6cef" />
    </svg>
  );
}
