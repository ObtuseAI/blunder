import { invoke } from "@tauri-apps/api/core";
import type { CoreResponse } from "./types";

interface CoreRequest {
  method: "GET";
  path: string;
  body: null;
}

const request = async <T>(input: CoreRequest): Promise<T> => {
  const response = await invoke<CoreResponse<T>>("core_request", { request: input });
  if (response.status < 200 || response.status >= 300) {
    const detail = JSON.stringify(response.body);
    throw new Error(`DumbMoney Core request failed with ${response.status}: ${detail}`);
  }
  return response.body;
};

export const getJson = async <T>(path: string): Promise<T> =>
  request<T>({ method: "GET", path, body: null });
