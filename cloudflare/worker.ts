import { Container } from "@cloudflare/containers";
import { env as workerEnv } from "cloudflare:workers";

type WorkerEnv = {
  ZOOM_ROOM_LIGHT: DurableObjectNamespace<ZoomRoomLightContainer>;
  HOST?: string;
  PORT?: string;
  ZOOM_WEBHOOK_SECRET_TOKEN?: string;
  ZOOM_ACCOUNT_ID?: string;
  ZOOM_CLIENT_ID?: string;
  ZOOM_CLIENT_SECRET?: string;
  ZOOM_SCHEDULE_USER_ID?: string;
  SCHEDULE_LOOKAHEAD_MINUTES?: string;
  SCHEDULE_POLL_SECONDS?: string;
};

declare global {
  namespace Cloudflare {
    interface Env extends WorkerEnv {}
  }
}

export class ZoomRoomLightContainer extends Container {
  defaultPort = 5050;
  sleepAfter = "15m";

  envVars = {
    HOST: workerEnv.HOST ?? "0.0.0.0",
    PORT: workerEnv.PORT ?? "5050",
    ZOOM_WEBHOOK_SECRET_TOKEN: workerEnv.ZOOM_WEBHOOK_SECRET_TOKEN ?? "",
    ZOOM_ACCOUNT_ID: workerEnv.ZOOM_ACCOUNT_ID ?? "",
    ZOOM_CLIENT_ID: workerEnv.ZOOM_CLIENT_ID ?? "",
    ZOOM_CLIENT_SECRET: workerEnv.ZOOM_CLIENT_SECRET ?? "",
    ZOOM_SCHEDULE_USER_ID: workerEnv.ZOOM_SCHEDULE_USER_ID ?? "me",
    SCHEDULE_LOOKAHEAD_MINUTES: workerEnv.SCHEDULE_LOOKAHEAD_MINUTES ?? "15",
    SCHEDULE_POLL_SECONDS: workerEnv.SCHEDULE_POLL_SECONDS ?? "60"
  };
}

export default {
  async fetch(request: Request, env: WorkerEnv): Promise<Response> {
    const container = env.ZOOM_ROOM_LIGHT.getByName("default-room");
    return container.fetch(request);
  }
};
