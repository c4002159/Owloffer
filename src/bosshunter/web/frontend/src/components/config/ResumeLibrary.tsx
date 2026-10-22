import { ResumeUploadSection } from '@/components/config/ResumeUploadSection'
import { ExtraResumesSection } from '@/components/config/ExtraResumesSection'

/** The default resume and the per-direction resumes as one library. */
export function ResumeLibrary({ currentResumePath, updateConfig }: { currentResumePath: string; updateConfig: (path: string, value: unknown) => void }) {
  return (
    <div data-tour="resume-upload" className="space-y-3 rounded-2xl border border-primary/30 bg-surface p-4">
      <div>
        <h3 className="text-sm font-black text-foreground">简历库</h3>
        <p className="mt-1 text-xs leading-5 text-muted">一份默认简历，加上按岗位方向分开的多份简历，都在这里管理。</p>
      </div>
      <ResumeUploadSection currentResumePath={currentResumePath} updateConfig={updateConfig} />
      <ExtraResumesSection />
    </div>
  )
}
