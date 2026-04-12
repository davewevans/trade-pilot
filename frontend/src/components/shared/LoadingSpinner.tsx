export function LoadingSpinner() {
  return (
    <div className="flex items-center justify-center py-12">
      <div
        className="w-6 h-6 rounded-full border-2 border-t-transparent animate-spin"
        style={{ borderColor: 'var(--accent)', borderTopColor: 'transparent' }}
      />
    </div>
  )
}
