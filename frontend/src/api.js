export async function getJson(path, signal) {
  const response = await fetch(`/api/v1/${path}`, { signal });
  if (!response.ok) throw new Error(`조회 실패 (HTTP ${response.status})`);
  return response.json();
}
