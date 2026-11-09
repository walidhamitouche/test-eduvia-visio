// Aperçu local du micro et de la caméra avant d'entrer (test, choix de l'appareil).
// Deux flux indépendants : une caméra absente n'empêche pas d'essayer le micro.

import { useEffect, useRef, useState } from "react";

export type DeviceProblem = "denied" | "unavailable" | null;

export interface DeviceOption {
  id: string;
  label: string;
}

function problemOf(error: unknown): DeviceProblem {
  if (error instanceof DOMException) {
    if (error.name === "NotAllowedError" || error.name === "SecurityError") return "denied";
  }
  return "unavailable";
}

function useStream(kind: "audio" | "video", enabled: boolean, deviceId: string | null) {
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [problem, setProblem] = useState<DeviceProblem>(null);

  useEffect(() => {
    let cancelled = false;
    let acquired: MediaStream | null = null;
    setStream(null);
    if (!enabled) return undefined;
    if (!navigator.mediaDevices?.getUserMedia) {
      setProblem("unavailable");
      return undefined;
    }
    const exact = deviceId ? { deviceId: { exact: deviceId } } : {};
    const constraints: MediaStreamConstraints =
      kind === "audio"
        ? { audio: { echoCancellation: true, noiseSuppression: true, ...exact } }
        : { video: { width: { ideal: 1280 }, height: { ideal: 720 }, ...exact } };
    navigator.mediaDevices
      .getUserMedia(constraints)
      .then((media) => {
        if (cancelled) {
          media.getTracks().forEach((track) => track.stop());
          return;
        }
        acquired = media;
        setProblem(null);
        setStream(media);
      })
      .catch((error: unknown) => {
        if (!cancelled) setProblem(problemOf(error));
      });
    return () => {
      cancelled = true;
      // Libère l'appareil : sous Windows, une caméra occupée ne peut pas être rouverte.
      acquired?.getTracks().forEach((track) => track.stop());
    };
  }, [kind, enabled, deviceId]);

  return { stream, problem };
}

/** Niveau sonore (0 à 1) d'un flux audio. */
function useAudioLevel(stream: MediaStream | null): number {
  const [level, setLevel] = useState(0);
  const last = useRef(0);

  useEffect(() => {
    if (!stream) {
      setLevel(0);
      return undefined;
    }
    const AudioContextClass: typeof AudioContext | undefined =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AudioContextClass) return undefined;
    const context = new AudioContextClass();
    const analyser = context.createAnalyser();
    analyser.fftSize = 512;
    context.createMediaStreamSource(stream).connect(analyser);
    const samples = new Uint8Array(analyser.fftSize);
    let frame = 0;
    const tick = () => {
      analyser.getByteTimeDomainData(samples);
      let sum = 0;
      for (const value of samples) {
        const centered = (value - 128) / 128;
        sum += centered * centered;
      }
      const next = Math.min(1, Math.sqrt(sum / samples.length) * 4);
      if (Math.abs(next - last.current) > 0.02) {
        last.current = next;
        setLevel(next);
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(frame);
      void context.close();
    };
  }, [stream]);

  return level;
}

export function useLocalPreview(opts: {
  micOn: boolean;
  camOn: boolean;
  micId: string | null;
  camId: string | null;
}) {
  const audio = useStream("audio", opts.micOn, opts.micId);
  const video = useStream("video", opts.camOn, opts.camId);
  const level = useAudioLevel(audio.stream);
  const [microphones, setMicrophones] = useState<DeviceOption[]>([]);
  const [cameras, setCameras] = useState<DeviceOption[]>([]);

  useEffect(() => {
    if (!navigator.mediaDevices?.enumerateDevices) return undefined;
    let cancelled = false;
    const refresh = async () => {
      const devices = await navigator.mediaDevices.enumerateDevices();
      if (cancelled) return;
      const toOptions = (kind: MediaDeviceKind) =>
        devices
          .filter((device) => device.kind === kind && device.deviceId)
          .map((device, index) => ({
            id: device.deviceId,
            label: device.label || `${kind === "audioinput" ? "Micro" : "Caméra"} ${index + 1}`,
          }));
      setMicrophones(toOptions("audioinput"));
      setCameras(toOptions("videoinput"));
    };
    void refresh().catch(() => undefined);
    navigator.mediaDevices.addEventListener?.("devicechange", refresh);
    return () => {
      cancelled = true;
      navigator.mediaDevices.removeEventListener?.("devicechange", refresh);
    };
    // Les libellés n'apparaissent qu'après l'autorisation : on relit quand un flux arrive.
  }, [audio.stream, video.stream]);

  return {
    audioStream: audio.stream,
    videoStream: video.stream,
    micProblem: audio.problem,
    camProblem: video.problem,
    level,
    microphones,
    cameras,
  };
}
