import { useRef, useState } from 'react';
import { Modal } from 'react-native';

import { PrivacyReviewPanel } from '@/components/PrivacyReviewModal';
import { PromptComparisonPanel } from '@/components/PromptComparisonPanel';
import { optimizePrompt, scanPrompt } from '@/services/api';
import { getOptimizerEnabled } from '@/services/preferences';
import type { OptimizeResult, PrivacyScan, PromptSource } from '@/types/chat';

/**
 * What the single review Modal is showing. Only one panel is ever visible, and `onApproved` is only called after the
 * exact text has passed the privacy check (and, if the optimizer suggested something, after the user chose a version).
 */
type Flow =
  | { kind: 'privacy'; stage: 'original' | 'selected'; text: string; scan: PrivacyScan; source: PromptSource }
  | { kind: 'compare'; original: string; originalSource: PromptSource; result: OptimizeResult };

const categoriesKey = (scan: PrivacyScan) => JSON.stringify(Object.entries(scan.categories).sort());

/** Shared by Chat and Research: privacy scan -> review -> optimizer comparison -> re-scan -> approved text. */
export function usePromptReview(onApproved: (text: string, source: PromptSource) => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [flow, setFlow] = useState<Flow | null>(null);
  const busyRef = useRef(false); // blocks double-taps while an async step is in flight
  const acknowledged = useRef<string | null>(null); // warn-level categories the user already confirmed
  const approvedRef = useRef(onApproved);
  approvedRef.current = onApproved; // always call the latest callback (it may close over fresh state)

  /** Runs one async step with a re-entrancy guard and a single error path. Nothing is sent if it fails. */
  const step = async (fn: () => Promise<void>) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setFlow(null);
      setError(`${e instanceof Error ? e.message : 'The check failed.'} Nothing was sent.`);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };

  const deliver = (text: string, source: PromptSource) => {
    setFlow(null);
    acknowledged.current = null;
    approvedRef.current(text, source);
  };

  const cancel = () => {
    setFlow(null); // nothing was sent; the caller's draft is untouched
    acknowledged.current = null;
  };

  /** After the original passed privacy review, offer an optimized version (if enabled and useful). */
  const offerOptimization = async (text: string, source: PromptSource) => {
    if (!(await getOptimizerEnabled())) return deliver(text, source);
    let result: OptimizeResult;
    try {
      result = await optimizePrompt(text);
    } catch {
      return deliver(text, source); // optimizer is optional; the text already passed the privacy check
    }
    if (!result.changed) return deliver(text, source);
    setFlow({ kind: 'compare', original: text, originalSource: source, result });
  };

  /** Step 1: scan the text on the backend BEFORE anything else. */
  const start = (raw: string) => {
    const text = raw.trim();
    if (!text) return;
    void step(async () => {
      const scan = await scanPrompt(text);
      if (scan.decision === 'allow') await offerOptimization(text, 'original');
      else setFlow({ kind: 'privacy', stage: 'original', text, scan, source: 'original' });
    });
  };

  /** The user picked a version: scan EXACTLY that text again before it can be used. */
  const useSelection = (text: string, source: PromptSource) =>
    void step(async () => {
      const scan = await scanPrompt(text);
      if (scan.decision === 'allow' || (scan.decision === 'warn' && acknowledged.current === categoriesKey(scan))) {
        deliver(text, source);
      } else {
        setFlow({ kind: 'privacy', stage: 'selected', text, scan, source });
      }
    });

  /** The privacy panel only calls this with text it has re-scanned and that policy permits. */
  const onPrivacyApproved = (finalText: string, scan: PrivacyScan) => {
    if (flow?.kind !== 'privacy') return;
    if (scan.decision === 'warn') acknowledged.current = categoriesKey(scan);
    const source: PromptSource = finalText === flow.text ? flow.source : 'edited';
    const stage = flow.stage;
    void step(async () => {
      if (stage === 'original') await offerOptimization(finalText, source);
      else deliver(finalText, source);
    });
  };

  const modal = (
    <Modal visible={flow !== null} animationType="slide" onRequestClose={cancel}>
      {flow?.kind === 'privacy' ? (
        <PrivacyReviewPanel
          key={`${flow.stage}:${flow.text}`}
          text={flow.text}
          initialScan={flow.scan}
          onCancel={cancel}
          onSend={onPrivacyApproved}
        />
      ) : null}
      {flow?.kind === 'compare' ? (
        <PromptComparisonPanel
          key={flow.original}
          original={flow.original}
          originalSource={flow.originalSource}
          result={flow.result}
          onCancel={cancel}
          onUse={useSelection}
        />
      ) : null}
    </Modal>
  );

  return { busy, error, setError, start, modal };
}
