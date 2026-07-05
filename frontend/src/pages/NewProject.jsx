import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { useDropzone } from 'react-dropzone'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { FileText, Upload, X, Sparkles, Lightbulb, Rocket, Wand2, ArrowRight, Loader2 } from 'lucide-react'

import { createProject } from '../api/projects'
import { submitTextRequirements, submitPdfRequirements } from '../api/requirements'
import { triggerStructuring } from '../api/structure'

function formatBytes(bytes) {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export default function NewProject() {
  const navigate = useNavigate()
  const qc = useQueryClient()

  // Persists across retries within one page mount — avoids double-creating a project.
  const projectIdRef = useRef(null)

  const [activeTab, setActiveTab] = useState('text')
  const [pdfFile, setPdfFile] = useState(null)
  const [pdfError, setPdfError] = useState(null)

  const {
    register,
    handleSubmit,
    watch,
    setError,
    clearErrors,
    formState: { errors },
  } = useForm({ defaultValues: { name: '', content: '' } })

  const content = watch('content')

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    accept: { 'application/pdf': ['.pdf'] },
    maxFiles: 1,
    maxSize: 10 * 1024 * 1024,
    onDrop: (accepted) => {
      if (accepted.length > 0) {
        setPdfFile(accepted[0])
        setPdfError(null)
      }
    },
    onDropRejected: (rejections) => {
      const msg = rejections[0]?.errors[0]?.message || 'File rejected'
      toast.error(msg)
    },
  })

  const submitMut = useMutation({
    mutationFn: async ({ name, mode, content, file }) => {
      // Step 1: create project (skip on retry if already created)
      if (!projectIdRef.current) {
        const project = await createProject(name)
        projectIdRef.current = project.id
      }
      const id = projectIdRef.current

      // Step 2: submit requirements
      if (mode === 'text') {
        await submitTextRequirements(id, content)
      } else {
        await submitPdfRequirements(id, file)
      }
    },
    onSuccess: async () => {
      qc.invalidateQueries({ queryKey: ['projects'] })
      toast.success('Project created!')
      const projectId = projectIdRef.current
      try {
        await triggerStructuring(projectId)
      } catch (err) {
        toast.error(err.friendlyMessage || 'Failed to start analysis. You can retry from the project page.')
      }
      navigate(`/projects/${projectId}`)
    },
    onError: (err) => {
      toast.error(err.friendlyMessage || 'Something went wrong. Please try again.')
    },
  })

  function switchTab(tab) {
    setActiveTab(tab)
    clearErrors('content')
    setPdfError(null)
  }

  function onSubmit(data) {
    if (activeTab === 'text' && data.content.length < 10) {
      setError('content', { message: 'Content must be at least 10 characters' })
      return
    }
    if (activeTab === 'pdf' && !pdfFile) {
      setPdfError('Please select a PDF file')
      return
    }
    setPdfError(null)
    submitMut.mutate({ name: data.name, mode: activeTab, content: data.content, file: pdfFile })
  }

  return (
    <div className="min-h-screen bg-surface-page relative overflow-x-hidden">
      {/* Ambient blobs — full page atmosphere */}
      <div className="fixed top-24 -left-40 w-96 h-96 rounded-full
                      bg-accent/[0.12] blur-3xl animate-pulse-slow pointer-events-none" />
      <div
        className="fixed bottom-0 -right-40 w-[28rem] h-[28rem] rounded-full
                   bg-accent-secondary/[0.12] blur-3xl animate-pulse-slow pointer-events-none"
        style={{ animationDelay: '3s' }}
      />
      <div className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2
                      w-96 h-96 rounded-full bg-accent/[0.05] blur-3xl pointer-events-none" />

      <main className="max-w-3xl mx-auto px-4 py-12 relative">

        {/* Header */}
        <div className="mb-8 text-center animate-fade-in">
          <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full
                          bg-accent/10 border border-accent/20
                          text-xs font-bold text-accent uppercase tracking-widest mb-4">
            <Sparkles className="w-3.5 h-3.5" />
            Start a new build
          </div>
          <h1 className="text-4xl md:text-5xl font-black text-text-default mb-3 leading-tight">
            Let&apos;s build{' '}
            <span className="bg-gradient-to-r from-accent to-accent-secondary bg-clip-text text-transparent">
              something new
            </span>
          </h1>
          <p className="text-base text-text-muted max-w-lg mx-auto">
            Give your project a name and describe what you want.
            DeMaestro handles the rest — from code to live URL.
          </p>
        </div>

        {/* Form card */}
        <div
          className="relative rounded-3xl border border-surface-border
                     bg-surface-panel/80 backdrop-blur-md
                     shadow-2xl shadow-accent/5 overflow-hidden
                     animate-fade-in"
          style={{ animationDelay: '150ms' }}
        >
          {/* Top gradient accent bar */}
          <div className="absolute inset-x-0 top-0 h-1
                          bg-gradient-to-r from-accent via-accent-secondary to-accent
                          bg-[length:200%_auto] animate-gradient" />

          {/* Corner shine ornaments */}
          <div className="absolute -top-8 -right-8 w-32 h-32 rounded-full
                          bg-accent/10 blur-2xl pointer-events-none" />
          <div className="absolute -bottom-8 -left-8 w-32 h-32 rounded-full
                          bg-accent-secondary/10 blur-2xl pointer-events-none" />

          <form onSubmit={handleSubmit(onSubmit)} className="relative p-8 md:p-10 space-y-8" noValidate>

            {/* Project name */}
            <div>
              <div className="flex items-baseline justify-between mb-2">
                <label className="block text-sm font-bold text-text-default">
                  Project name
                  <span className="text-accent ml-1">*</span>
                </label>
                <span className="text-xs text-text-muted">This becomes your app&apos;s name</span>
              </div>
              <div className="relative group">
                <input
                  {...register('name', {
                    required: 'Project name is required',
                    maxLength: { value: 80, message: 'Name must be 80 characters or fewer' },
                  })}
                  className="w-full px-5 py-4 rounded-2xl
                             bg-surface-page/60 border border-surface-border
                             text-lg text-text-default placeholder:text-text-muted/50
                             focus:outline-none focus:ring-2 focus:ring-accent/40
                             focus:border-accent transition-all duration-200"
                  placeholder="e.g. Focus List"
                  autoFocus
                />
                <Wand2 className="absolute right-5 top-1/2 -translate-y-1/2 w-5 h-5
                                   text-accent/40 group-focus-within:text-accent
                                   transition-colors pointer-events-none" />
              </div>
              {errors.name && (
                <p className="text-xs text-red-500 mt-2 flex items-center gap-1.5 animate-fade-in">
                  <span className="w-1.5 h-1.5 rounded-full bg-red-500" />
                  {errors.name.message}
                </p>
              )}
            </div>

            {/* Requirements section */}
            <div>
              <div className="flex items-baseline justify-between mb-3">
                <label className="block text-sm font-bold text-text-default">
                  Requirements
                  <span className="text-accent ml-1">*</span>
                </label>
                <span className="text-xs text-text-muted">Type them or upload a PDF</span>
              </div>

              {/* Tab toggle — sliding indicator */}
              <div className="relative flex rounded-2xl border border-surface-border
                              bg-surface-page/40 p-1 mb-4">
                {/* Sliding gradient pill */}
                <div
                  className="absolute top-1 bottom-1 rounded-xl
                             bg-gradient-to-br from-accent to-accent-secondary
                             shadow-lg shadow-accent/25
                             transition-all duration-300 ease-out"
                  style={{
                    width: 'calc(50% - 4px)',
                    left: activeTab === 'text' ? '4px' : 'calc(50% + 0px)',
                  }}
                />

                <button
                  type="button"
                  onClick={() => switchTab('text')}
                  className={`relative z-10 flex flex-1 items-center justify-center gap-2
                             rounded-xl px-4 py-3 text-sm font-semibold
                             transition-colors duration-300
                             ${activeTab === 'text' ? 'text-white' : 'text-text-muted hover:text-text-default'}`}
                >
                  <FileText className="w-4 h-4" />
                  Type requirements
                </button>

                <button
                  type="button"
                  onClick={() => switchTab('pdf')}
                  className={`relative z-10 flex flex-1 items-center justify-center gap-2
                             rounded-xl px-4 py-3 text-sm font-semibold
                             transition-colors duration-300
                             ${activeTab === 'pdf' ? 'text-white' : 'text-text-muted hover:text-text-default'}`}
                >
                  <Upload className="w-4 h-4" />
                  Upload PDF
                </button>
              </div>

              {/* Text tab */}
              {activeTab === 'text' && (
                <div className="animate-fade-in">
                  <textarea
                    {...register('content')}
                    className="w-full px-5 py-4 rounded-2xl
                               bg-surface-page/60 border border-surface-border
                               text-text-default placeholder:text-text-muted/50
                               focus:outline-none focus:ring-2 focus:ring-accent/40
                               focus:border-accent transition-all duration-200
                               min-h-[220px] resize-y leading-relaxed"
                    placeholder="Describe the app you want to build. For example: 'A recipe app where users can save and tag recipes, search by ingredient, and share favorites with friends.'"
                  />
                  <div className="flex items-start justify-between mt-2">
                    {errors.content ? (
                      <p className="text-xs text-red-500 flex items-center gap-1.5 animate-fade-in">
                        <span className="w-1.5 h-1.5 rounded-full bg-red-500" />
                        {errors.content.message}
                      </p>
                    ) : (
                      <p className="text-xs text-text-muted flex items-center gap-1.5">
                        <Lightbulb className="w-3.5 h-3.5" />
                        Tip: mention the app name, main features, and any style preference
                      </p>
                    )}
                    <p className={`text-xs tabular-nums font-medium
                                  ${content.length > 45000 ? 'text-amber-500' : 'text-text-muted'}`}>
                      {content.length.toLocaleString()} / 50,000
                    </p>
                  </div>
                </div>
              )}

              {/* PDF tab */}
              {activeTab === 'pdf' && (
                <div className="space-y-3 animate-fade-in">
                  {!pdfFile ? (
                    <div
                      {...getRootProps()}
                      className={`group relative border-2 border-dashed rounded-2xl
                                 p-10 text-center cursor-pointer transition-all duration-200
                                 ${isDragActive
                                   ? 'border-accent bg-accent/5 scale-[1.01]'
                                   : 'border-surface-border hover:border-accent/50 hover:bg-accent/5'}`}
                    >
                      <input {...getInputProps()} />
                      <div className={`w-16 h-16 mx-auto mb-4 rounded-2xl
                                      bg-gradient-to-br from-accent to-accent-secondary
                                      flex items-center justify-center
                                      shadow-xl shadow-accent/25
                                      transition-transform duration-300
                                      ${isDragActive ? 'scale-110' : 'group-hover:scale-105'}`}>
                        <Upload className="w-7 h-7 text-white" />
                      </div>
                      <p className="text-base font-semibold text-text-default mb-1">
                        {isDragActive ? 'Drop it here…' : 'Drop your PDF here'}
                      </p>
                      <p className="text-sm text-text-muted mb-2">or click to browse</p>
                      <p className="text-xs text-text-muted/70">PDF only · max 10 MB</p>
                    </div>
                  ) : (
                    <div className="flex items-center gap-4 p-4 rounded-2xl
                                    bg-gradient-to-br from-accent/5 to-accent-secondary/5
                                    border border-accent/20 animate-fade-in">
                      <div className="w-11 h-11 rounded-xl bg-gradient-to-br
                                      from-accent to-accent-secondary
                                      flex items-center justify-center
                                      shadow-lg shadow-accent/20 flex-shrink-0">
                        <FileText className="w-5 h-5 text-white" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-semibold text-text-default truncate">{pdfFile.name}</p>
                        <p className="text-xs text-text-muted">{formatBytes(pdfFile.size)} · Ready to submit</p>
                      </div>
                      <button
                        type="button"
                        onClick={() => setPdfFile(null)}
                        className="w-8 h-8 rounded-lg text-text-muted
                                   hover:text-red-500 hover:bg-red-500/10
                                   flex items-center justify-center
                                   transition-colors flex-shrink-0"
                        aria-label="Remove file"
                      >
                        <X className="w-4 h-4" />
                      </button>
                    </div>
                  )}
                  {pdfError && (
                    <p className="text-xs text-red-500 flex items-center gap-1.5 animate-fade-in">
                      <span className="w-1.5 h-1.5 rounded-full bg-red-500" />
                      {pdfError}
                    </p>
                  )}
                </div>
              )}
            </div>

            {/* What happens next */}
            <div className="flex items-center gap-4 p-4 rounded-2xl
                            bg-gradient-to-br from-accent/5 to-accent-secondary/5
                            border border-accent/15">
              <div className="w-10 h-10 rounded-xl bg-accent/10
                              flex items-center justify-center flex-shrink-0">
                <Rocket className="w-5 h-5 text-accent" />
              </div>
              <div className="flex-1">
                <p className="text-xs font-bold text-accent uppercase tracking-widest mb-0.5">
                  What happens next
                </p>
                <p className="text-sm text-text-muted">
                  Answer a few quick questions, review the plan, and DeMaestro
                  builds and deploys your app automatically.
                </p>
              </div>
            </div>

            {/* Submit button */}
            <button
              type="submit"
              disabled={submitMut.isPending}
              className="group relative w-full overflow-hidden
                         px-8 py-4 rounded-2xl
                         bg-gradient-to-r from-accent via-accent-secondary to-accent
                         bg-[length:200%_auto]
                         text-white font-bold text-base
                         shadow-2xl shadow-accent/30
                         hover:shadow-2xl hover:shadow-accent/45
                         hover:scale-[1.01] hover:bg-[position:100%_50%]
                         active:scale-[0.99]
                         disabled:opacity-60 disabled:cursor-not-allowed disabled:scale-100
                         transition-all duration-300
                         flex items-center justify-center gap-2"
            >
              {/* Hover shine sweep */}
              <span className="absolute inset-0 pointer-events-none
                               bg-gradient-to-r from-transparent via-white/20 to-transparent
                               -translate-x-full group-hover:translate-x-full
                               transition-transform duration-1000" />

              {submitMut.isPending ? (
                <>
                  <Loader2 className="w-5 h-5 animate-spin relative z-10" />
                  <span className="relative z-10">Creating your project…</span>
                </>
              ) : (
                <>
                  <Sparkles className="w-5 h-5 relative z-10" />
                  <span className="relative z-10">Create project &amp; submit requirements</span>
                  <ArrowRight className="w-4 h-4 relative z-10 group-hover:translate-x-1 transition-transform" />
                </>
              )}
            </button>
          </form>
        </div>

        {/* Trailing hint chips */}
        <div
          className="mt-6 flex flex-wrap items-center justify-center gap-2 animate-fade-in"
          style={{ animationDelay: '300ms' }}
        >
          <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full
                           bg-surface-panel/60 border border-surface-border
                           text-xs text-text-muted">
            <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
            Full-stack + database + auth
          </span>
          <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full
                           bg-surface-panel/60 border border-surface-border
                           text-xs text-text-muted">
            <span className="w-1.5 h-1.5 rounded-full bg-accent-secondary animate-pulse" />
            Deployed to a live URL
          </span>
          <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full
                           bg-surface-panel/60 border border-surface-border
                           text-xs text-text-muted">
            <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
            Around 5 minutes
          </span>
        </div>
      </main>
    </div>
  )
}
