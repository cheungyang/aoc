import discord
from discord.ui import View, Button, Select

class PollButtonView(View):
    def __init__(self, poll_data, channel):
        super().__init__(timeout=None) # Persistent view
        self.poll_data = poll_data
        self.channel = channel
        
        options = (poll_data.get("options") or [])[:25]
        for option in options:
            label = option.get("text") or ""
            if len(label) > 80:
                label = label[:77] + "..."
            emoji = option.get("emoji") if option.get("emoji") else None
            if not label and not emoji:
                label = "Select"
            button = Button(label=label if label else None, emoji=emoji)
            button.callback = self.create_callback(option.get("response", ""))
            self.add_item(button)

    def create_callback(self, response_text):
        async def callback(interaction: discord.Interaction):
            # Send the response text to the channel
            # We mention the user to make it clear who voted
            message_text = f"{interaction.user.mention}: {response_text}"
            await self.channel.send(message_text)
            # Acknowledge the interaction
            await interaction.response.defer()
        return callback

class PollSelectView(View):
    def __init__(self, poll_data, channel):
        super().__init__(timeout=None)
        self.poll_data = poll_data
        self.channel = channel
        
        options = []
        raw_options = (poll_data.get("options") or [])[:25]
        for i, option in enumerate(raw_options):
            label = option.get("text") or ""
            if len(label) > 100:
                label = label[:97] + "..."
            if not label:
                label = f"Option {i + 1}"
            options.append(discord.SelectOption(
                label=label,
                value=str(i), # Use index as value
                emoji=option.get("emoji") if option.get("emoji") else None
            ))
            
        if options:
            select = Select(
                placeholder="Select options...",
                min_values=1,
                max_values=len(options),
                options=options
            )
            select.callback = self.callback
            self.add_item(select)

    async def callback(self, interaction: discord.Interaction):
        # Get selected options
        selected_indices = [int(val) for val in interaction.data["values"]]
        responses = [self.poll_data["options"][i]["response"] for i in selected_indices if i < len(self.poll_data.get("options", []))]
        
        # Combine responses
        message_text = f"{interaction.user.mention}: " + ", ".join(responses)
        await self.channel.send(message_text)
        # Acknowledge the interaction
        await interaction.response.defer()
