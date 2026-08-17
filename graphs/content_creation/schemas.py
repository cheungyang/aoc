from pydantic import BaseModel, Field

class PlotAudit(BaseModel):
    is_approved: bool = Field(description="True if approved, False if rejected")
    rejection_target: str = Field(description="IMAGE, PLOT, or BOTH")
    revision_notes: str = Field(description="Detailed feedback for the rejected assets")
    markdown_report: str = Field(description="The full human-readable markdown audit report")

class VideoPlot(BaseModel):
    title: str = Field(description="Title of the video plot")
    source_image: str = Field(description="Path to the source image")
    source_audio: str = Field(description="Path to the source audio")
    motion_prompt: str = Field(description="Motion prompt for Google Veo 3")
    overlay_text: str = Field(description="Text overlay for the video")
    markdown_content: str = Field(description="The full human-readable markdown presentation")

class FinalCopy(BaseModel):
    caption: str = Field(description="The polished social media caption")
    hashtags: list[str] = Field(description="List of hashtags")
    markdown_content: str = Field(description="Full markdown payload")
