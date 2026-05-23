import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { X, Plus, Trash2, Loader2, Pencil, MessageSquare } from 'lucide-react'

import { getClarificationHistory, reviseRequirements } from '../api/structure'

export default function EditRequirementsModal({ projectId, open, onClose }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [answers, setAnswers] = useState([])      // [{ ambiguity_id, question, answer }]
  const [newReqs, setNewReqs] = useState([''])

  const { data: history = [], isLoading } = useQuery({
    queryKey: ['clarification-history', projectId],
    queryFn: () => getClarificationHistory(projectId),
    enabled: open,
  })

  // Seed editable answers from fetched history whenever the modal (re)opens.
  useEffect(() => {
    if (open) {
      setAnswers(
        (history ?? []).map((t) => ({
          ambiguity_id: t.ambiguity_id ?? null,
          question: t.question ?? '',
          answer: t.answer ?? '',
          suggested_options: t.suggested_options ?? [],
        })),
      )
      setNewReqs([''])
    }
  }, [open, history])

  const reviseMut = useMutation({
    mutationFn: () =>
      reviseRequirements(projectId, {
        answers,
        newRequirements: newReqs.map((r) => r.trim()).filter(Boolean),
      }),
    onSuccess: () => {
      toast.success('Re-analyzing your updated requirements…')
      qc.invalidateQueries({ queryKey: ['project', projectId] })
      onClose()
      navigate(`/projects/${projectId}`)   // ProjectChat handles structuring→clarifying→approval
    },
    onError: (err) =>
      toast.error(err.friendlyMessage || err.message || 'Could not save changes'),
  })

  if (!open) return null

  const hasSomething =
    answers.some((a) => a.answer.trim()) || newReqs.some((r) => r.trim())

  function setAnswer(i, val) {
    setAnswers((prev) => prev.map((a, idx) => (idx === i ? { ...a, answer: val } : a)))
  }
  function setReq(i, val) {
    setNewReqs((prev) => prev.map((r, idx) => (idx === i ? val : r)))
  }
  function addReq() {
    setNewReqs((prev) => [...prev, ''])
  }
  function removeReq(i) {
    setNewReqs((prev) => (prev.length === 1 ? [''] : prev.filter((_, idx) => idx !== i)))
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className="bg-white dark:bg-slate-800 rounded-xl shadow-2xl w-full max-w-2xl max-h-[85vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between px-6 py-4 border-b border-slate-200 dark:border-slate-700">
          <div>
            <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-2">
              <Pencil className="w-5 h-5 text-primary-600" />
              Edit requirements
            </h2>
            <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">
              Change your earlier answers or add new requirements. We&apos;ll
              re-analyze everything and rebuild the summary.
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 transition-colors"
            aria-label="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Body */}
        <div className="px-6 py-5 overflow-y-auto space-y-6">
          {/* Answers section */}
          <section>
            <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100 flex items-center gap-2 mb-3">
              <MessageSquare className="w-4 h-4 text-primary-500" />
              Your answers
            </h3>
            {isLoading ? (
              <div className="flex items-center gap-2 text-slate-500 dark:text-slate-400 text-sm">
                <Loader2 className="w-4 h-4 animate-spin" /> Loading your answers…
              </div>
            ) : answers.length === 0 ? (
              <p className="text-sm text-slate-500 dark:text-slate-400">
                No clarification questions were asked for this project.
              </p>
            ) : (
              <div className="space-y-4">
                {answers.map((a, i) => (
                  <div key={i} className="rounded-lg border border-slate-200 dark:border-slate-700 p-3">
                    <p className="text-sm font-medium text-slate-800 dark:text-slate-100 mb-2">{a.question}</p>
                    {(a.suggested_options ?? []).length > 0 && (
                      <div className="flex flex-wrap gap-2 mb-2">
                        {a.suggested_options.map((opt) => (
                          <button
                            key={opt}
                            type="button"
                            onClick={() => setAnswer(i, opt)}
                            className={`rounded-full px-3 py-1 text-xs transition-colors ${
                              a.answer === opt
                                ? 'bg-primary-600 text-white'
                                : 'bg-primary-50 dark:bg-primary-600/20 text-primary-700 dark:text-primary-200 hover:bg-primary-100 dark:hover:bg-primary-600/30'
                            }`}
                          >
                            {opt}
                          </button>
                        ))}
                      </div>
                    )}
                    <textarea
                      value={a.answer}
                      onChange={(e) => setAnswer(i, e.target.value)}
                      rows={2}
                      className="input w-full resize-y"
                      placeholder="Your answer…"
                    />
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* New requirements section */}
          <section>
            <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100 flex items-center gap-2 mb-3">
              <Plus className="w-4 h-4 text-primary-500" />
              Add new requirements
            </h3>
            <div className="space-y-2">
              {newReqs.map((r, i) => (
                <div key={i} className="flex items-center gap-2">
                  <input
                    value={r}
                    onChange={(e) => setReq(i, e.target.value)}
                    className="input flex-1"
                    placeholder="e.g. Users should be able to export their data as CSV"
                  />
                  <button
                    onClick={() => removeReq(i)}
                    className="text-slate-400 hover:text-red-500 transition-colors p-2"
                    aria-label="Remove"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              ))}
            </div>
            <button
              onClick={addReq}
              className="mt-2 text-sm text-primary-600 hover:text-primary-700 font-medium flex items-center gap-1"
            >
              <Plus className="w-4 h-4" /> Add another requirement
            </button>
          </section>
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-3 px-6 py-4 border-t border-slate-200 dark:border-slate-700">
          <button onClick={onClose} className="btn-secondary" disabled={reviseMut.isPending}>
            Cancel
          </button>
          <button
            onClick={() => reviseMut.mutate()}
            disabled={!hasSomething || reviseMut.isPending}
            className="btn-primary"
          >
            {reviseMut.isPending ? (
              <>
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                Saving…
              </>
            ) : (
              'Save & re-analyze'
            )}
          </button>
        </div>
      </div>
    </div>
  )
}
