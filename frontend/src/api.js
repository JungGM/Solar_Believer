export async function getJson(path, signal) {
  try {
    const timeout = AbortSignal.timeout(15000);
    const response = await fetch(`/api/v1/${path}`, {
      signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
    });
    if (!response.ok) throw new Error(`조회 실패 (HTTP ${response.status})`);
    return response.json();
  } catch (error) {
    if (error.name === "TimeoutError")
      throw new Error("서버 응답이 지연되고 있습니다 (15초)");
    throw error;
  }
}
