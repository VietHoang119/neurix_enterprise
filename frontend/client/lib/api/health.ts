import { httpClient } from "./client";

export interface HealthResponse {
  status: string;
  uid: string;
}

export const healthApi = {
  check: () => httpClient.get<HealthResponse>("/health"),
};
