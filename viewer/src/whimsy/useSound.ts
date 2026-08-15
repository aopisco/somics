/** Tiny synthesised sound effects, off unless the user asks for them.
 *
 * Synthesised rather than sampled so the repo carries no audio files. The AudioContext is
 * created on first playback, not on mount: browsers refuse to start one before a user
 * gesture, and creating it eagerly logs a warning on every load.
 */

import { useCallback, useEffect, useRef } from "react";

import { useStore } from "../state";

type Voice = "squeak" | "whoosh";

interface Shape {
  type: OscillatorType;
  from: number;
  to: number;
  seconds: number;
  gain: number;
}

const SHAPES: Record<Voice, Shape> = {
  squeak: { type: "triangle", from: 900, to: 1650, seconds: 0.09, gain: 0.05 },
  whoosh: { type: "sawtooth", from: 320, to: 90, seconds: 0.35, gain: 0.03 },
};

export interface Sound {
  play: (voice: Voice) => void;
}

export function useSound(): Sound {
  const enabled = useStore((s) => s.sound);
  const context = useRef<AudioContext | null>(null);

  useEffect(() => {
    return () => {
      void context.current?.close();
      context.current = null;
    };
  }, []);

  const play = useCallback(
    (voice: Voice) => {
      if (!enabled) return;
      const Ctor = window.AudioContext ?? window.webkitAudioContext;
      if (!Ctor) return;
      context.current ??= new Ctor();
      const audio = context.current;
      if (audio.state === "suspended") void audio.resume();

      const shape = SHAPES[voice];
      const now = audio.currentTime;
      const oscillator = audio.createOscillator();
      const envelope = audio.createGain();

      oscillator.type = shape.type;
      oscillator.frequency.setValueAtTime(shape.from, now);
      oscillator.frequency.exponentialRampToValueAtTime(shape.to, now + shape.seconds);
      envelope.gain.setValueAtTime(shape.gain, now);
      envelope.gain.exponentialRampToValueAtTime(0.0001, now + shape.seconds);

      oscillator.connect(envelope).connect(audio.destination);
      oscillator.start(now);
      oscillator.stop(now + shape.seconds);
    },
    [enabled],
  );

  return { play };
}

declare global {
  interface Window {
    webkitAudioContext?: typeof AudioContext;
  }
}
